#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/project_files.py
"""Project-scoped, read-only file access for the Storage app (List + Read +
Download).

The first vertical slice of compass \u00a714 Storage: the storage surface is scoped
to the requester's ACTIVE PROJECT and built on the ``scitex_app`` SDK file
primitives, rather than a second user/project model of its own.

CONTRACT (consumed from the hub, never re-implemented here)
----------------------------------------------------------
scitex-hub owns the project model, the user model, and the authz policy.
This module takes exactly ONE input from the hub: the Django ``request``.
From it it resolves the requester's current project via the hub's
``get_current_project`` and roots every file operation at
``project.get_local_path()``.

Authz is DELEGATED to that one call, not re-implemented. The hub's own
``resolve_user_working_dir`` documents that ``get_current_project`` "enforces
``can_view``": it resolves only a project the authenticated user may view
(owner or collaborator), preferring the header selector over the session. So
"cross-user denial" is a property of the resolver, not of this module:
user B's request resolves to B's project (a different root), and B's API
simply cannot reach A's files. A leaf app that kept its own Project/User model
would re-derive the policy and drift from it \u2014 the exact failure compass
\u00a714 L477 forbids. Hence there is NO ``from apps.infra... import Project``
here; the hub's object is used duck-typed through the two primitives named
above (``get_current_project``, ``get_local_path``).

HUB vs STANDALONE
-----------------
``resolve_project_scope`` locates ``get_current_project`` by a small set of
candidate import paths (the canonical one is ``apps.infra.project_app.
services.project_utils``). Inside the hub that resolves. Under the standalone
launcher (``scitex-storage start-gui``) there is no ``apps.infra`` on the path,
so it degrades to ``None`` \u2014 the no-silent-fallback rule the existing scan
views already apply \u2014 and the API answers an explicit ``no_project`` 404
instead of a bare 500.

FILE PRIMITIVES
---------------
``build_backend(scope)`` returns ``scitex_app.sdk.get_files(scope.project_dir)``
\u2014 the SDK's ``FilesBackend``. With no ``SCITEX_API_TOKEN`` in the
environment it is the local-disk ``FileSystemBackend`` rooted at the project
directory; if the hub sets the token it becomes the cloud backend, so these
views are backend-agnostic. ``FileSystemBackend._resolve`` raises
``ValueError`` on any path that escapes the root, which :func:`_open` maps to
``PermissionDenied`` (fail-closed traversal denial).

ERRORS (typed, never a bare 500)
--------------------------------
:exc:`StorageFileError` is the base; ``code`` + ``status`` are the handler
contract. Subclasses:

  * ``NoProject``         \u2014 404 ``no_project``          (no resolvable project)
  * ``ProjectNotFound``   \u2014 404 ``project_not_found``   (path does not exist)
  * ``PermissionDenied``  \u2014 403 ``permission_denied``   (escapes the project root)
  * ``NotAFile``          \u2014 400 ``not_a_file``          (target is a directory)
  * ``DiskFull``          \u2014 507 ``disk_full``           (write space exhausted)
  * ``InvalidPath``       \u2014 400 ``invalid_path``        (malformed / non-utf-8)

``views.py`` maps each ``code`` to its HTTP status via :data:`STATUS_BY_CODE`,
so a new error kind that lacks a mapping fails loudly.
"""

from __future__ import annotations

import errno
import mimetypes
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from django.http import HttpResponse

# The SDK public primitive, not the private backend, so a future scitex-app
# with a stable file API keeps these views working.
from scitex_app.sdk import get_files

__all__ = [
    "ProjectScope",
    "StorageFileError",
    "NoProject",
    "ProjectNotFound",
    "PermissionDenied",
    "NotAFile",
    "DiskFull",
    "WriteError",
    "FileConflict",
    "InvalidPath",
    "resolve_project_scope",
    "build_backend",
    "list_files",
    "read_file",
    "download_file",
    "write_file",
    "rename_file",
    "delete_file",
    "make_dir",
    "STATUS_BY_CODE",
]

#: HTTP status for each typed error ``code`` (see module docstring).
STATUS_BY_CODE = {
    "no_project": 404,
    "project_not_found": 404,
    "permission_denied": 403,
    "not_a_file": 400,
    "disk_full": 507,
    "invalid_path": 400,
    "write_error": 500,
    "file_conflict": 409,
}

#: Candidate import locations for the hub's ``get_current_project``. Only the
#: canonical one needs to exist for the hub to work; the rest are defensive
#: ordering in case the hub relocates the service without a version bump.
_GET_CURRENT_PROJECT_CANDIDATES = (
    "apps.infra.project_app.services.project_utils",
    "apps.infra.project_app.services",
)

# The seam tests (and a future hub that ships a public project resolver)
# monkeypatch. Production keeps the default, which does the lazy import.
_GET_CURRENT_PROJECT: Optional[Callable] = None


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProjectScope:
    """The requester's active project, resolved from the hub ``request``.

    ``project_dir`` is the on-disk root every file operation is contained
    within. ``project_id`` / ``slug`` / ``name`` are opaque identifiers
    surfaced in responses so the client can tell WHICH project it is in
    without learning the project model's shape.
    """

    project_dir: Path
    project_id: Optional[str] = None
    slug: Optional[str] = None
    name: Optional[str] = None


def resolve_project_scope(request) -> Optional[ProjectScope]:
    """Resolve the requester's current project to a :class:`ProjectScope`.

    Returns ``None`` when no project is in scope \u2014 the request has no
    authenticated user, this runs under the standalone launcher (no hub on the
    import path), or the hub has no project the user may view. Callers treat
    ``None`` as :class:`NoProject`. The hub's ``get_current_project`` enforces
    ``can_view`` internally, so a returned scope is already one the user may
    see; this function adds no project/user of its own.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    fn = _current_project_fn()
    if fn is None:
        return None

    try:
        project = fn(request, user=user)
    except Exception:
        # A project-resolution failure inside the hub must not become a bare
        # 500 in a leaf app; report it as "no project in scope".
        return None
    if project is None:
        return None

    project_dir = _project_dir(project)
    if project_dir is None:
        return None

    return ProjectScope(
        project_dir=project_dir,
        project_id=_attr_str(project, "id"),
        slug=_attr_str(project, "slug"),
        name=_attr_str(project, "name"),
    )


def _current_project_fn() -> Optional[Callable]:
    """The hub's ``get_current_project``, or ``None`` if it is not importable.

    A module-level seam (``_GET_CURRENT_PROJECT``) takes precedence \u2014 that is
    how tests inject a fake without a database. In production it stays
    ``None`` and the lazy import below runs; any candidate module that fails to
    import (standalone launcher) degrades to ``None`` rather than a 500.
    """
    if _GET_CURRENT_PROJECT is not None:
        return _GET_CURRENT_PROJECT
    for module_path in _GET_CURRENT_PROJECT_CANDIDATES:
        try:
            module = __import__(module_path, fromlist=["get_current_project"])
        except (ImportError, ModuleNotFoundError):
            continue
        fn = getattr(module, "get_current_project", None)
        if callable(fn):
            return fn
    return None


def _project_dir(project) -> Optional[Path]:
    """The on-disk project root, or ``None`` if it cannot be resolved.

    Prefers the hub's ``get_local_path()`` (a resolved ``Path``); falls back
    to the raw ``local_path`` field. Returns ``None`` rather than an empty
    root so the caller can report :class:`NoProject`.
    """
    getter = getattr(project, "get_local_path", None)
    if callable(getter):
        try:
            return Path(str(getter())).resolve()
        except Exception:
            pass
    raw = getattr(project, "local_path", None)
    if raw:
        try:
            return Path(str(raw)).resolve()
        except Exception:
            return None
    return None


def _attr_str(obj, name: str) -> Optional[str]:
    value = getattr(obj, name, None)
    return None if value is None else str(value)


# --------------------------------------------------------------------------- #
# Typed errors
# --------------------------------------------------------------------------- #
class StorageFileError(Exception):
    """Base class for every project-file failure the API can return.

    ``code`` is the stable machine name the client switches on; ``status`` is
    its HTTP mapping (kept here so a handler and a test agree without a second
    lookup table).
    """

    code: str = "storage_error"
    status: int = 500

    def __init__(self, message: str, *, detail: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_payload(self) -> dict:
        return {"error": self.code, "message": self.message, **self.detail}


class NoProject(StorageFileError):
    code = "no_project"
    status = STATUS_BY_CODE["no_project"]


class ProjectNotFound(StorageFileError):
    code = "project_not_found"
    status = STATUS_BY_CODE["project_not_found"]


class PermissionDenied(StorageFileError):
    code = "permission_denied"
    status = STATUS_BY_CODE["permission_denied"]


class NotAFile(StorageFileError):
    code = "not_a_file"
    status = STATUS_BY_CODE["not_a_file"]


class DiskFull(StorageFileError):
    code = "disk_full"
    status = STATUS_BY_CODE["disk_full"]


class InvalidPath(StorageFileError):
    code = "invalid_path"
    status = STATUS_BY_CODE["invalid_path"]


class WriteError(StorageFileError):
    """A write failed for a reason other than full disk / containment.

    Still TYPED (a JSON ``{error, message}`` response), so it satisfies the
    "never a bare 500" contract -- a bare 500 is an unhandled stack trace,
    not a structured response. Status 500 is honest here: the server could
    not complete the write for a reason the caller cannot fix by retrying
    with a different path.
    """

    code = "write_error"
    status = STATUS_BY_CODE["write_error"]


class FileConflict(StorageFileError):
    """A rename/move would overwrite an existing file (SDK ``rename`` raises
    ``FileExistsError`` when the destination already exists).

    409 is the HTTP code for a state conflict the client must resolve -- pick
    a different destination or use an explicit overwrite (out of scope for this
    slice).
    """

    code = "file_conflict"
    status = STATUS_BY_CODE["file_conflict"]


# --------------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------------- #
def build_backend(scope: ProjectScope):
    """The ``scitex_app`` file backend rooted at the scope's project dir.

    Local by default; cloud when ``SCITEX_API_TOKEN`` is set \u2014 the SDK's
    ``get_files`` auto-detection decides, this module stays backend-agnostic.
    """
    return get_files(scope.project_dir)


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
def _scope_or_no_project(request) -> ProjectScope:
    scope = resolve_project_scope(request)
    if scope is None:
        raise NoProject("no project in scope for this request")
    return scope


def _contained(root: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` against ``root``, failing closed on escape.

    Raises :class:`InvalidPath` for an empty/non-string path and
    :class:`PermissionDenied` for a path that resolves outside ``root``
    (``..``, absolute paths, symlinks out). Both are checked BEFORE any
    filesystem access so a hostile ``?path=`` never touches the disk.
    """
    if not isinstance(rel_path, str):
        raise InvalidPath("path must be a string")
    rel_path = rel_path.strip()
    if not rel_path:
        raise InvalidPath("path is empty")
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise PermissionDenied(
            f"path escapes the project root: {rel_path!r}"
        )
    return candidate


def _contained_for_write(root: Path, rel_path: str) -> Path:
    """:func:`_contained` PLUS the write-only ancestor check.

    The SDK's ``write`` creates parent directories with ``mkdir(parents=True,
    exist_ok=True)`` -- but if any EXISTING path component is a regular FILE,
    that call raises ``FileExistsError`` (characterized last pass), which is not
    a ``StorageFileError`` and would escape the view as a bare 500. We close
    that class here, before touching the disk: walk the ancestors of the target
    up to the root and deny when an existing ancestor is a file, a symlink that
    does not resolve to a directory, or a broken symlink. A MISSING ancestor is
    allowed -- the SDK creates it.

    Symlink-escape (target resolving outside the root) is already caught by
    :func:`_contained` (it ``resolve()``s).
    """
    target = _contained(root, rel_path)
    root_resolved = root.resolve()
    # Walk from the target's immediate parent up to (and including) root.
    # Deny only when an existing ancestor is NOT a directory -- i.e. it is a
    # regular file, a symlink-to-file, or a broken symlink. A MISSING ancestor
    # is allowed: the SDK's mkdir(parents=True) creates it. This closes the
    # characterized bare-500 class (parent-is-file -> FileExistsError) without
    # breaking the fresh-nested-directory write.
    parent = target.parent
    for ancestor in (parent, *parent.parents):
        if ancestor == root_resolved:
            break
        if ancestor.is_dir():
            continue  # existing directory -- fine
        if ancestor.is_symlink():
            raise PermissionDenied(
                f"target path passes through a symlink that is not a directory: "
                f"{rel_path!r}"
            )
        if ancestor.exists():
            raise PermissionDenied(
                f"target path passes through a file: {rel_path!r}"
            )
        # else: missing -- the SDK will create it; allow.
    return target


def _open(scope: ProjectScope, rel_path: str, *, binary: bool = False):
    """Read one file via the SDK backend, mapping failures to typed errors.

    The SDK backend already rejects traversal with ``ValueError``; we also
    pre-check with :func:`_contained` so the denial is explicit and does not
    depend on a particular backend's wording.
    """
    _contained(scope.project_dir, rel_path)
    backend = build_backend(scope)
    try:
        return backend.read(rel_path, binary=binary)
    except PermissionDenied:
        raise
    except FileNotFoundError as exc:
        raise ProjectNotFound(str(exc)) from exc
    except IsADirectoryError as exc:
        # read/download on a directory is a caller error, not a crash —
        # mapping it here keeps the "never a bare 500" contract that would
        # otherwise be broken by the SDK backend raising IsADirectoryError.
        raise NotAFile(f"target is a directory, not a file: {rel_path!r}") from exc
    except UnicodeDecodeError as exc:
        # binary=False read of a non-UTF-8 file. This slice's read endpoint is
        # a TEXT reader; a binary artifact belongs on /download.
        # (Order matters: UnicodeDecodeError subclasses ValueError, so it must
        # be caught before the traversal guard below or it is unreachable.)
        raise InvalidPath(
            f"not a UTF-8 text file: {rel_path!r} (use download for binaries)"
        ) from exc
    except ValueError as exc:
        # FileSystemBackend._resolve's traversal guard reaches here if the
        # pre-check and the backend disagree; treat as denied, not 500.
        raise PermissionDenied(str(exc)) from exc
    except OSError as exc:
        if exc.errno in (28,):  # ENOSPC
            raise DiskFull(str(exc)) from exc
        raise


def list_files(request, rel_path: str = "") -> dict:
    """One-level directory listing (files AND sub-directories) as a dict.

    Returns ``{"project": {...}, "path": rel_path, "entries": [...]}`` where
    each entry is ``{"name", "path", "type": "file"|"dir"}``. Directories come
    from ``os.scandir`` so the SDK's file-only ``list()`` is not the
    bottleneck for a browser.
    """
    scope = _scope_or_no_project(request)
    target = _contained(scope.project_dir, rel_path or ".")
    try:
        if not target.exists():
            raise ProjectNotFound(f"directory not found: {rel_path!r}")
        if not target.is_dir():
            raise NotAFile(f"not a directory: {rel_path!r}")
        entries = []
        with os.scandir(target) as it:
            for entry in sorted(it, key=lambda e: (not e.is_dir(), e.name.lower())):
                entries.append({
                    "name": entry.name,
                    "path": _relative(scope.project_dir, entry.path),
                    "type": "dir" if entry.is_dir() else "file",
                })
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    return {
        "project": _scope_summary(scope),
        "path": rel_path,
        "entries": entries,
    }


def read_file(request, rel_path: str) -> dict:
    """Read one text file, returning ``{"path", "content", "size"}``."""
    scope = _scope_or_no_project(request)
    content = _open(scope, rel_path, binary=False)
    if not isinstance(content, str):
        content = str(content)
    return {
        "project": _scope_summary(scope),
        "path": rel_path,
        "content": content,
        "size": len(content.encode("utf-8")),
    }


def download_file(request, rel_path: str) -> HttpResponse:
    """Stream one file as an attachment, with a safe Content-Disposition.

    Binary-safe (reads ``binary=True``) so any project artifact \u2014 not just
    text \u2014 can be pulled out of the browser.
    """
    scope = _scope_or_no_project(request)
    data = _open(scope, rel_path, binary=True)
    if isinstance(data, str):
        data = data.encode("utf-8")

    name = os.path.basename(rel_path) or "download"
    content_type, _ = mimetypes.guess_type(name)
    if content_type is None:
        # Unrecognised extension -> octet-stream forces a download rather than
        # letting the browser sniff it as text and mangle binary.
        content_type = "application/octet-stream"

    response = HttpResponse(data, content_type=content_type)
    response["Content-Length"] = str(len(data))
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return response


def write_file(request, rel_path: str, content: str) -> dict:
    """Write one text file inside the current project, atomically.

    Safe-atomic contract (the write half of compass \u00a714 L493):
      * scope + authz from the hub resolver (``_scope_or_no_project``) -- no
        second user/project model;
      * path containment via :func:`_contained_for_write` (denies ``..``,
        absolute, symlink-out, AND any path whose ancestor is a file/broken
        symlink -- the SDK's ``mkdir(parents=True)`` ``FileExistsError`` class);
      * the file is written to a temp file in the SAME directory then
        ``os.replace``d over the target, so a mid-write failure never leaves a
        partial/corrupt target (crash-safe atomicity);
      * ENOSPC -> :class:`DiskFull` (507); the SDK's traversal ``ValueError``
        and any containment violation -> typed 4xx, never a bare 500.

    Returns ``{"project", "path", "size"}``.
    """
    scope = _scope_or_no_project(request)
    target = _contained_for_write(scope.project_dir, rel_path)
    # The SDK has no atomic-write API; this is the safe wrapper over the same
    # backend. Create the (verified-file-free) missing parents, then write to a
    # temp file in target's dir so os.replace is atomic on the same filesystem.
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp_", suffix=".writing")
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(target))
    except OSError as exc:
        # clean up the temp file on any failure, then translate
        _unlink_quiet(tmp)
        if exc.errno in (28,):  # ENOSPC
            raise DiskFull(f"no space to write {rel_path!r}") from exc
        raise WriteError(f"could not write {rel_path!r}: {exc}") from exc
    except Exception:
        _unlink_quiet(tmp)
        raise
    return {
        "project": _scope_summary(scope),
        "path": rel_path,
        "size": len(content.encode("utf-8")),
    }


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _resolve_entry(scope: ProjectScope, rel_path: str) -> Path:
    """Resolve ``rel_path`` to an EXISTING entry (file OR directory) in scope.

    Compass line 495 (folder operations) generalizes the L494 file-only
    resolver: rename and delete now operate on a file *or* a directory. A
    missing target -> :class:`ProjectNotFound`; a containment escape
    (``..`` / absolute / symlink-out) -> :class:`PermissionDenied` via
    :func:`_contained`.
    """
    target = _contained(scope.project_dir, rel_path)
    if not target.exists():
        raise ProjectNotFound(f"not found: {rel_path!r}")
    return target


def rename_file(request, old_rel: str, new_rel: str) -> dict:
    """Rename / move one file OR directory within the current project.

    The SDK's ``rename`` is the move primitive (a cross-directory rename on the
    same filesystem), so this single operation covers both the Rename and Move
    halves, for files and for folders (compass line 494 rename/move + line 495
    folder rename/move). Both source AND destination are containment-checked;
    the source must exist; an existing destination (file or dir) is a typed
    :class:`FileConflict` (409), not a silent overwrite.

    Returns ``{"project", "from", "to"}``.
    """
    scope = _scope_or_no_project(request)
    _resolve_entry(scope, old_rel)  # raises if missing / containment-escape
    # The destination may not exist (that's a rename); check its containment.
    dest = _contained(scope.project_dir, new_rel)
    if dest.exists():
        raise FileConflict(f"destination already exists: {new_rel!r}")
    backend = build_backend(scope)
    try:
        backend.rename(old_rel, new_rel)
    except FileNotFoundError as exc:
        raise ProjectNotFound(str(exc)) from exc
    except FileExistsError as exc:
        raise FileConflict(
            f"destination already exists: {new_rel!r}"
        ) from exc
    except ValueError as exc:
        # SDK traversal guard (source or dest escaping the root).
        raise PermissionDenied(str(exc)) from exc
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    return {
        "project": _scope_summary(scope),
        "from": old_rel,
        "to": new_rel,
    }


def delete_file(request, rel_path: str) -> dict:
    """Delete one file OR directory from the current project.

    The project root itself is never a valid target.  The walk is anchored to
    an open descriptor for that root and refuses symlinked parent components.
    A symlink in the final component is unlinked as an entry; its referent is
    never resolved or traversed.  Directories are recursively removed using
    descriptor-relative operations with ``follow_symlinks=False``.

    Returns ``{"project", "path"}``.
    """
    scope = _scope_or_no_project(request)
    parts = _delete_parts(rel_path)
    _require_safe_delete_primitives()
    root = scope.project_dir
    nofollow_flags = _directory_open_flags()

    descriptors: list[int] = []
    try:
        parent_fd = _open_verified_project_root(root)
        descriptors.append(parent_fd)
        for component in parts[:-1]:
            try:
                parent_fd = os.open(component, nofollow_flags, dir_fd=parent_fd)
            except FileNotFoundError as exc:
                raise ProjectNotFound(f"not found: {rel_path!r}") from exc
            except (NotADirectoryError, PermissionError) as exc:
                raise PermissionDenied(
                    f"delete path has an unsafe parent component: {rel_path!r}"
                ) from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise PermissionDenied(
                        f"delete path has a symlink parent: {rel_path!r}"
                    ) from exc
                raise WriteError(f"could not inspect {rel_path!r}: {exc}") from exc
            descriptors.append(parent_fd)
        _delete_entry_at(parent_fd, parts[-1], rel_path)
    except FileNotFoundError as exc:
        raise ProjectNotFound(f"not found: {rel_path!r}") from exc
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except OSError as exc:
        raise WriteError(f"could not access project root: {exc}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return {
        "project": _scope_summary(scope),
        "path": rel_path,
    }


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


def _safe_delete_primitives_available(
    *,
    dir_fd_support=None,
    fd_support=None,
    follow_symlinks_support=None,
    has_flags: Optional[bool] = None,
) -> bool:
    """Return whether this runtime can enforce descriptor-anchored deletion."""
    dir_fd_support = os.supports_dir_fd if dir_fd_support is None else dir_fd_support
    fd_support = os.supports_fd if fd_support is None else fd_support
    follow_symlinks_support = (
        os.supports_follow_symlinks
        if follow_symlinks_support is None
        else follow_symlinks_support
    )
    if has_flags is None:
        has_flags = all(
            hasattr(os, flag)
            for flag in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW")
        )
    required_dir_fd = {os.open, os.stat, os.unlink, os.rmdir, os.rename, os.mkdir}
    return (
        has_flags
        and required_dir_fd.issubset(dir_fd_support)
        and os.listdir in fd_support
        and os.stat in follow_symlinks_support
    )


def _require_safe_delete_primitives(available: Optional[bool] = None) -> None:
    if available is None:
        available = _safe_delete_primitives_available()
    if not available:
        raise WriteError(
            "safe project deletion is unavailable on this platform; no files changed"
        )


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _open_verified_project_root(
    root: Path, expected_stat: Optional[os.stat_result] = None
) -> int:
    """Open the canonical root without following a replacement symlink."""
    try:
        observed = expected_stat or os.stat(root, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise NoProject("project root does not exist") from exc
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except OSError as exc:
        raise WriteError(f"could not inspect project root: {exc}") from exc
    if not stat.S_ISDIR(observed.st_mode):
        raise PermissionDenied("project root is not a physical directory")

    try:
        root_fd = os.open(root, _directory_open_flags())
    except FileNotFoundError as exc:
        raise NoProject("project root does not exist") from exc
    except (NotADirectoryError, PermissionError) as exc:
        raise PermissionDenied("project root changed before deletion") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PermissionDenied("project root changed to a symlink") from exc
        raise WriteError(f"could not open project root safely: {exc}") from exc

    opened = os.fstat(root_fd)
    if not _same_inode(observed, opened):
        os.close(root_fd)
        raise FileConflict("project root changed before deletion")
    return root_fd


def _delete_parts(rel_path: str) -> tuple[str, ...]:
    """Validate a delete path without resolving its final symlink."""
    if not isinstance(rel_path, str):
        raise InvalidPath("path must be a string")
    rel_path = rel_path.strip()
    if not rel_path:
        raise InvalidPath("path is empty")
    if "\x00" in rel_path:
        raise InvalidPath("path contains a null byte")

    path = Path(rel_path)
    if path.is_absolute() or ".." in path.parts:
        raise PermissionDenied(f"path escapes the project root: {rel_path!r}")
    if not path.parts:
        raise PermissionDenied("refusing to delete the project root")
    return path.parts


def _delete_entry_at(parent_fd: int, name: str, display_path: str) -> None:
    """Remove ``name`` below ``parent_fd`` without following symlinks."""
    try:
        entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ProjectNotFound(f"not found: {display_path!r}") from exc
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except OSError as exc:
        raise WriteError(f"could not inspect {display_path!r}: {exc}") from exc

    if not stat.S_ISDIR(entry_stat.st_mode):
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise ProjectNotFound(f"not found: {display_path!r}") from exc
        except IsADirectoryError as exc:
            raise FileConflict(
                f"target changed while it was being deleted: {display_path!r}"
            ) from exc
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                raise DiskFull(str(exc)) from exc
            raise WriteError(f"could not delete {display_path!r}: {exc}") from exc
        return

    _delete_directory_at(parent_fd, name, display_path, entry_stat)


def _delete_directory_at(
    parent_fd: int,
    name: str,
    display_path: str,
    expected_stat: os.stat_result,
) -> None:
    """Remove a directory only while it retains its observed identity."""
    try:
        child_fd = os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    except FileNotFoundError as exc:
        raise ProjectNotFound(f"not found: {display_path!r}") from exc
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except NotADirectoryError as exc:
        raise FileConflict(
            f"target changed while it was being deleted: {display_path!r}"
        ) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise FileConflict(
                f"target changed while it was being deleted: {display_path!r}"
            ) from exc
        raise WriteError(f"could not inspect {display_path!r}: {exc}") from exc

    try:
        opened_stat = os.fstat(child_fd)
        if not _same_inode(opened_stat, expected_stat):
            raise FileConflict(
                f"target changed while it was being deleted: {display_path!r}"
            )
        stage_fd, stage_name = _stage_directory_at(
            parent_fd, name, display_path, opened_stat
        )
        try:
            _delete_staged_directory(
                parent_fd,
                child_fd,
                stage_fd,
                stage_name,
                display_path,
                opened_stat,
            )
        finally:
            os.close(stage_fd)
    except FileNotFoundError as exc:
        raise ProjectNotFound(f"not found: {display_path!r}") from exc
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except OSError as exc:
        raise WriteError(f"could not delete {display_path!r}: {exc}") from exc
    finally:
        os.close(child_fd)


def _stage_directory_at(
    parent_fd: int,
    name: str,
    display_path: str,
    expected_stat: os.stat_result,
) -> tuple[int, str]:
    """Atomically move a directory away from its public name before removal."""
    for _attempt in range(8):
        stage_name = f".scitex-storage-delete-{secrets.token_hex(16)}"
        try:
            os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
            break
        except FileExistsError:
            continue
    else:
        raise WriteError("could not allocate a private delete staging directory")

    stage_fd: Optional[int] = None
    try:
        stage_stat = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        stage_fd = os.open(stage_name, _directory_open_flags(), dir_fd=parent_fd)
        if not _same_inode(stage_stat, os.fstat(stage_fd)):
            raise FileConflict("delete staging directory changed unexpectedly")
        os.rename(name, "target", src_dir_fd=parent_fd, dst_dir_fd=stage_fd)
        moved_stat = os.stat("target", dir_fd=stage_fd, follow_symlinks=False)
        if not _same_inode(moved_stat, expected_stat):
            os.close(stage_fd)
            stage_fd = None
            raise FileConflict(
                f"target changed while it was being deleted: {display_path!r}; "
                f"replacement preserved in {stage_name!r}"
            )
        return stage_fd, stage_name
    except StorageFileError:
        raise
    except FileNotFoundError as exc:
        raise ProjectNotFound(f"not found: {display_path!r}") from exc
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except OSError as exc:
        raise WriteError(f"could not stage {display_path!r}: {exc}") from exc
    finally:
        if stage_fd is not None and "moved_stat" not in locals():
            os.close(stage_fd)


def _delete_staged_directory(
    parent_fd: int,
    child_fd: int,
    stage_fd: int,
    stage_name: str,
    display_path: str,
    expected_stat: os.stat_result,
) -> None:
    """Remove a verified directory from its private, unpredictable staging path."""
    for child_name in os.listdir(child_fd):
        _delete_entry_at(child_fd, child_name, f"{display_path}/{child_name}")
    current_stat = os.stat("target", dir_fd=stage_fd, follow_symlinks=False)
    if not _same_inode(current_stat, expected_stat):
        raise FileConflict(
            f"staged target changed while it was being deleted: {display_path!r}"
        )
    os.rmdir("target", dir_fd=stage_fd)
    os.rmdir(stage_name, dir_fd=parent_fd)


def make_dir(request, rel_path: str) -> dict:
    """Create one (optionally nested) directory in the current project.

    Compass line 495 (folder operations) -- the create half. The destination
    must not already exist (an existing dir -> :class:`FileConflict` 409; an
    existing file -> :class:`NotAFile` 400). Path containment is enforced by
    :func:`_contained_for_write` (the same guard the write path uses), so
    ``..`` / absolute / symlink-out / under-file are all denied before any
    disk access. Missing intermediate directories are created (``parents=True``).

    Returns ``{"project", "path"}``.
    """
    scope = _scope_or_no_project(request)
    target = _contained_for_write(scope.project_dir, rel_path)
    if target.exists():
        if target.is_dir():
            raise FileConflict(f"directory already exists: {rel_path!r}")
        raise NotAFile(f"a file already exists at: {rel_path!r}")
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        if exc.errno in (28,):  # ENOSPC
            raise DiskFull(f"no space to create directory {rel_path!r}") from exc
        raise WriteError(f"could not create directory {rel_path!r}: {exc}") from exc
    return {
        "project": _scope_summary(scope),
        "path": rel_path,
    }


def _relative(root: Path, target: str) -> str:
    p = Path(target)
    try:
        return str(p.relative_to(root))
    except ValueError:
        return p.name


def _scope_summary(scope: ProjectScope) -> dict:
    return {"id": scope.project_id, "slug": scope.slug, "name": scope.name}

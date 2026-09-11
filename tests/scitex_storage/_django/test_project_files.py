#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the project-scoped file API (List + Read + Download).

compass \u00a714 Storage: the storage surface is scoped to the requester's ACTIVE
PROJECT via the hub's ``get_current_project`` (which enforces ``can_view``),
rooted at ``project.get_local_path()``, and built on the ``scitex_app`` SDK
file primitives. These tests exercise that contract WITHOUT a database or the
hub repo: a fake ``get_current_project`` is injected through the module-level
seam ``project_files._GET_CURRENT_PROJECT`` and returns a fake project whose
``get_local_path()`` points at a real temp directory.

What the tests prove (the cross-user story):
  * user A resolves to project A; user B resolves to project B.
  * B asking for a path that lives in A's project gets ``not_found`` (the path
    doesn't exist under B's root) or B's OWN content if the same relative path
    exists in B -- never A's bytes.
  * Anonymous / no-project / traversal / directory-read all fail CLOSED to a
    typed 4xx, never a bare 500.

Test discipline (PA-307 STX-TQ002/TQ007): every test carries the three AAA
markers and exactly ONE top-level assertion; compound checks are built into a
value first, then asserted once.
"""

from __future__ import annotations

import json
import os

import pytest


def _boot_django_for_storage_gui():
    """Shared helper -- NOT a test, does not itself need AAA markers."""
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings"
    )
    import django

    django.setup()


def _j(response):
    """Parse a JSON response's body (Django 6's JsonResponse has no .json())."""
    return json.loads(response.content)


# --------------------------------------------------------------------------- #
# Fakes: user, project, and the injected get_current_project
# --------------------------------------------------------------------------- #
class _User:
    def __init__(self, username):
        self.username = username
        self.is_authenticated = True


class _Project:
    def __init__(self, project_id, slug, name, root):
        self.id = project_id
        self.slug = slug
        self.name = name
        self._root = root

    def get_local_path(self):
        return self._root


def _make_project(tmp_path, tag):
    """A project dir with a unique-by-tag layout the assertions can key on."""
    root = tmp_path / f"proj_{tag}"
    root.mkdir(parents=True)
    (root / "shared.txt").write_text("common line\n", encoding="utf-8")
    (root / f"{tag}_secret.txt").write_text(f"TOPSECRET-{tag}\n", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "inner.txt").write_text(f"inner-{tag}\n", encoding="utf-8")
    return _Project(f"id-{tag}", tag, f"Project {tag}", root)


def _request_for(user, path=""):
    from django.test import RequestFactory

    request = RequestFactory().get("/storage/api/list", {"path": path})
    request.user = user
    return request


def _post_request_for(user, body):
    """A POST with a JSON body -- for the write endpoint."""
    from django.test import RequestFactory

    data = json.dumps(body)
    request = RequestFactory().post(
        "/storage/api/write", data=data, content_type="application/json"
    )
    request.user = user
    return request


@pytest.fixture
def project_pair(tmp_path):
    """Two independent project roots + users + a resolver mapping user->proj."""
    proj_a = _make_project(tmp_path, "A")
    proj_b = _make_project(tmp_path, "B")

    def fake_get_current_project(request, user=None):
        user = user or getattr(request, "user", None)
        return {"alice": proj_a, "bob": proj_b}.get(user.username)

    return {
        "proj_a": proj_a,
        "proj_b": proj_b,
        "user_a": _User("alice"),
        "user_b": _User("bob"),
        "resolver": fake_get_current_project,
    }


@pytest.fixture
def _with_resolver(project_pair):
    """Inject the fake resolver and clean up afterwards."""
    from scitex_storage._django import project_files

    original = project_files._GET_CURRENT_PROJECT
    project_files._GET_CURRENT_PROJECT = project_pair["resolver"]
    try:
        yield project_pair
    finally:
        project_files._GET_CURRENT_PROJECT = original


# --------------------------------------------------------------------------- #
# LIST
# --------------------------------------------------------------------------- #
def test_list_types_of_every_entry_are_files_and_dirs(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_list

    # Act
    payload = _j(project_list(_request_for(project_pair["user_a"], "")))
    types = {e["name"]: e["type"] for e in payload["entries"]}

    # Assert -- one-level listing: the root holds the dir + two files; the
    # file nested in sub/ is NOT listed here (it belongs to the "sub" descent).
    assert types == {
        "sub": "dir",
        "shared.txt": "file",
        "A_secret.txt": "file",
    }


def test_list_is_scoped_to_the_requester_project_not_the_other(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_list

    # Act
    payload = _j(project_list(_request_for(project_pair["user_a"], "")))
    names = {e["name"] for e in payload["entries"]}

    # Assert -- A's own secret is listed, B's is not (cross-tenant isolation).
    assert ("A_secret.txt" in names) and ("B_secret.txt" not in names)


def test_list_surfaces_project_identity_not_model_shape(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_list

    # Act
    payload = _j(project_list(_request_for(project_pair["user_a"], "")))
    project = payload["project"]

    # Assert
    assert project == {"id": "id-A", "slug": "A", "name": "Project A"}


def test_list_descends_into_a_subdirectory(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_list

    # Act
    payload = _j(project_list(_request_for(project_pair["user_a"], "sub")))
    names = {e["name"] for e in payload["entries"]}
    requested_path = payload["path"]

    # Assert
    assert names == {"inner.txt"} and requested_path == "sub"


def test_list_of_missing_dir_is_not_found_404(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_list

    # Act
    response = project_list(_request_for(project_pair["user_a"], "does-not-exist"))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (404, "project_not_found")


def test_list_absolute_path_denied_403(project_pair, _with_resolver):
    """An absolute ``?path=`` escapes the project root -> fail closed."""
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_list

    # Act
    response = project_list(_request_for(project_pair["user_a"], "/etc/passwd"))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (403, "permission_denied")


def test_list_traversal_denied_403(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_list

    # Act
    response = project_list(_request_for(project_pair["user_a"], "../proj_B"))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (403, "permission_denied")


# --------------------------------------------------------------------------- #
# READ  (+ the cross-user isolation property)
# --------------------------------------------------------------------------- #
def test_read_own_file_returns_content(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_read

    # Act
    payload = _j(project_read(_request_for(project_pair["user_a"], "A_secret.txt")))
    result = (
        payload["project"]["slug"],
        "TOPSECRET-A" in payload["content"],
        payload["path"],
    )

    # Assert
    assert result == ("A", True, "A_secret.txt")


def test_cross_user_denied_cannot_read_other_users_file(
    project_pair, _with_resolver
):
    """Bob resolving to project B cannot read Alice's A_secret.txt.

    The heart of the "no second user/project model" contract: the denial is a
    property of the hub resolver scoping Bob to B's root, so a path that exists
    in A is simply ``not_found`` under B -- never A's bytes.
    """
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_read

    # Act
    response = project_read(_request_for(project_pair["user_b"], "A_secret.txt"))
    body = response.content.decode()
    result = (response.status_code, _j(response)["error"], "TOPSECRET-A" in body)

    # Assert
    assert result == (404, "project_not_found", False)


def test_cross_user_same_relative_path_returns_own_content(
    project_pair, _with_resolver
):
    """Where the SAME relative path exists in both projects, Bob gets B's OWN
    content -- never A's."""
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_read

    # Act
    payload = _j(project_read(_request_for(project_pair["user_b"], "shared.txt")))
    result = (payload["project"]["slug"], "TOPSECRET" in payload["content"])

    # Assert
    assert result == ("B", False)


def test_read_nonexistent_file_is_404(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_read

    # Act
    response = project_read(_request_for(project_pair["user_a"], "nope.txt"))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (404, "project_not_found")


def test_read_traversal_denied_403(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_read

    # Act
    response = project_read(
        _request_for(project_pair["user_a"], "../proj_B/B_secret.txt")
    )
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (403, "permission_denied")


def test_read_binary_file_is_not_text_400(project_pair, _with_resolver):
    """A non-UTF-8 artifact is not a text file -> 400, pointing at download."""
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_read

    (project_pair["proj_a"]._root / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe")

    # Act
    response = project_read(_request_for(project_pair["user_a"], "blob.bin"))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (400, "invalid_path")


def test_read_on_directory_is_400_not_bare_500(project_pair, _with_resolver):
    """Regression: read of a directory is a typed 400 (NotAFile), never the
    IsADirectoryError -> bare 500 the "no bare 500" rule forbids."""
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_read

    # Act
    response = project_read(_request_for(project_pair["user_a"], "sub"))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (400, "not_a_file")


# --------------------------------------------------------------------------- #
# DOWNLOAD
# --------------------------------------------------------------------------- #
def test_download_streams_exact_bytes(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_download

    (project_pair["proj_a"]._root / "data.csv").write_bytes(b"a,b\n1,2\n")

    # Act
    response = project_download(_request_for(project_pair["user_a"], "data.csv"))
    result = (response.status_code, response.content)

    # Assert
    assert result == (200, b"a,b\n1,2\n")


def test_download_sets_safe_attachment_disposition(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_download

    (project_pair["proj_a"]._root / "data.csv").write_bytes(b"a,b\n1,2\n")

    # Act
    response = project_download(_request_for(project_pair["user_a"], "data.csv"))
    disposition = response["Content-Disposition"]
    is_attachment = disposition.startswith("attachment") and "data.csv" in disposition
    result = (is_attachment, response["Content-Type"])

    # Assert -- an attachment (not inline) with a sniffed, not injected, name.
    assert result == (True, "text/csv")


def test_download_of_missing_is_404(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_download

    # Act
    response = project_download(_request_for(project_pair["user_a"], "ghost.bin"))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (404, "project_not_found")


# --------------------------------------------------------------------------- #
# NO PROJECT / ANONYMOUS -> typed 404, never 500
# --------------------------------------------------------------------------- #
def test_anonymous_request_is_no_project_404(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from django.test import RequestFactory
    from scitex_storage._django.views import project_list

    request = RequestFactory().get("/storage/api/list", {"path": ""})
    request.user = None  # anonymous

    # Act
    response = project_list(request)
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (404, "no_project")


def test_no_resolved_project_is_404(tmp_path):
    """A hub where get_current_project returns None (user has no project)."""
    _boot_django_for_storage_gui()
    # Arrange
    from django.test import RequestFactory
    from scitex_storage._django import project_files
    from scitex_storage._django.views import project_list

    def fake(request, user=None):
        return None

    original = project_files._GET_CURRENT_PROJECT
    project_files._GET_CURRENT_PROJECT = fake

    # Act
    try:
        request = RequestFactory().get("/storage/api/list", {"path": ""})
        request.user = _User("carol")
        response = project_list(request)
    finally:
        project_files._GET_CURRENT_PROJECT = original
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (404, "no_project")


def test_standalone_no_hub_is_no_project_404():
    """Under the standalone launcher there is no ``apps.infra`` on the path, so
    the resolver degrades to no_project (404), not a ModuleNotFoundError 500.
    The seam defaults to None (nothing injected) and no hub module imports.
    """
    _boot_django_for_storage_gui()
    # Arrange
    from django.test import RequestFactory
    from scitex_storage._django import project_files
    from scitex_storage._django.views import project_list

    request = RequestFactory().get("/storage/api/list", {"path": ""})
    request.user = _User("dave")

    # Act
    response = project_list(request)
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (404, "no_project")


# --------------------------------------------------------------------------- #
# WRITE  (compass §14 L493 write-half)
# --------------------------------------------------------------------------- #
def test_write_creates_a_nested_file(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_write

    # Act
    response = project_write(_post_request_for(project_pair["user_a"], {
        "path": "newdir/newfile.txt", "content": "hello write\n",
    }))
    written = (project_pair["proj_a"]._root / "newdir" / "newfile.txt")
    result = (
        response.status_code,
        _j(response)["path"],
        _j(response)["project"]["slug"],
        written.read_text(encoding="utf-8"),
    )

    # Assert
    assert result == (200, "newdir/newfile.txt", "A", "hello write\n")


def test_write_overwrites_an_existing_file_atomically(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_write

    # Act
    response = project_write(_post_request_for(project_pair["user_a"], {
        "path": "shared.txt", "content": "replaced\n",
    }))
    after = (project_pair["proj_a"]._root / "shared.txt").read_text(encoding="utf-8")
    leftovers = [
        p.name for p in project_pair["proj_a"]._root.iterdir()
        if p.name.startswith(".tmp_")
    ]
    result = (response.status_code, after, leftovers)

    # Assert -- replaced, and no temp file left behind (atomic replace).
    assert result == (200, "replaced\n", [])


def test_write_under_a_file_is_denied_403(project_pair, _with_resolver):
    """The characterized defect: a path whose ancestor is a FILE would make
    the SDK's mkdir(parents=True) raise FileExistsError -> a bare 500. Now it
    is a typed 403 before any disk access."""
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_write

    # "shared.txt" is an existing file in A's root; writing under it is the
    # characterized FileExistsError -> bare-500 defect, now a typed 403.
    # Act
    response = project_write(_post_request_for(project_pair["user_a"], {
        "path": "shared.txt/inner.txt", "content": "x\n",
    }))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (403, "permission_denied")


def test_write_traversal_denied_403(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_write

    # Act
    response = project_write(_post_request_for(project_pair["user_a"], {
        "path": "../proj_B/evil.txt", "content": "x\n",
    }))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (403, "permission_denied")


def test_write_symlink_escape_denied_403(project_pair, _with_resolver):
    """A symlink INSIDE the project pointing OUTSIDE it must be denied.

    The target resolves outside the root, so os.replace would land the file
    outside the project. The guard's resolve() catches this.
    """
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_write

    escape_target = project_pair["proj_b"]._root / "leaked.txt"
    link = project_pair["proj_a"]._root / "sneaky"
    link.symlink_to(project_pair["proj_b"]._root)

    # Act
    response = project_write(_post_request_for(project_pair["user_a"], {
        "path": "sneaky/leaked.txt", "content": "x\n",
    }))
    result = (response.status_code, _j(response)["error"], escape_target.exists())

    # Assert
    assert result == (403, "permission_denied", False)


def test_cross_user_cannot_write_into_another_project(project_pair, _with_resolver):
    """Bob (project B) writing a path that would land in A is denied by scope;
    A's tree is byte-identical afterwards."""
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_write

    a_root = project_pair["proj_a"]._root
    a_before = sorted(str(p.relative_to(a_root)) for p in a_root.rglob("*"))

    # Act
    project_write(_post_request_for(project_pair["user_b"], {
        "path": "A_secret.txt", "content": "I stole this\n",
    }))
    a_after = sorted(str(p.relative_to(a_root)) for p in a_root.rglob("*"))
    result = (
        (a_root / "A_secret.txt").read_text(encoding="utf-8"),
        a_before == a_after,
    )

    # Assert -- A's file untouched and A's tree unchanged.
    assert result == ("TOPSECRET-A\n", True)


def test_write_missing_content_is_400(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django.views import project_write

    # Act
    response = project_write(_post_request_for(project_pair["user_a"], {
        "path": "x.txt",
    }))
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (400, "invalid_path")


def test_write_invalid_json_is_400(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from django.test import RequestFactory
    from scitex_storage._django.views import project_write

    request = RequestFactory().post(
        "/storage/api/write", data="{not json", content_type="application/json"
    )
    request.user = project_pair["user_a"]

    # Act
    response = project_write(request)
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (400, "invalid_path")


def test_write_disk_full_error_is_typed_507_not_bare(project_pair, _with_resolver):
    """The disk-full failure is TYPED (507 ``disk_full``), not a bare 500.

    The actual ENOSPC trigger is environmental (a genuinely full disk) and
    cannot be honestly forced here -- and PA-306 bans mocking. So this test
    proves the CONTRACT instead: ``DiskFull`` is a ``StorageFileError`` whose
    code/status map to a 507 JSON response (not an unhandled traceback), and
    ``STATUS_BY_CODE`` carries it. The view's ``except OSError: if errno==28:
    raise DiskFull`` is the environmental branch this contract documents.
    """
    _boot_django_for_storage_gui()
    # Arrange
    from scitex_storage._django import project_files

    err = project_files.DiskFull("no space left on device")

    # Act
    result = (
        isinstance(err, project_files.StorageFileError),
        err.code,
        err.status,
        project_files.STATUS_BY_CODE["disk_full"],
    )

    # Assert
    assert result == (True, "disk_full", 507, 507)


def test_write_on_anonymous_request_is_no_project_404(project_pair, _with_resolver):
    _boot_django_for_storage_gui()
    # Arrange
    from django.test import RequestFactory
    from scitex_storage._django.views import project_write

    data = json.dumps({"path": "x.txt", "content": "y"})
    request = RequestFactory().post("/storage/api/write", data=data, content_type="application/json")
    request.user = None  # anonymous

    # Act
    response = project_write(request)
    result = (response.status_code, _j(response)["error"])

    # Assert
    assert result == (404, "no_project")

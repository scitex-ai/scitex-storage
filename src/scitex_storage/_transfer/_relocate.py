#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply a declarative move plan to a tree: simulate first, then move reversibly.

The mechanism behind ``scitex-storage relocate``. A reorganization is a list of
ordered moves, plus junk to clear and paths to drop. Applied by hand, that list
fails in three ways this module rules out:

1. **The preview lies.** Checking each move against the REAL disk means a later
   parent move still "sees" children that an earlier move already relocated.
   So the plan is SIMULATED, in order, on a virtual index of the tree. Every
   later move sees the effect of every earlier one, and the dry run reports
   exactly what ``--yes`` will do.
2. **A merge clobbers.** Moving into a directory that already exists has to
   merge. A file that already exists at the destination is either byte-identical
   (the source copy is a duplicate, archived reversibly) or different (a
   CONFLICT). Any conflict aborts the WHOLE plan before the first mutation.
3. **Cleanup is final.** Junk, drops and duplicates are never unlinked. They move
   into an archive under the run id with their relative path preserved, the same
   reversible move-aside rule as ``reclaim``. The relocate manifest records every
   operation in order, so ``relocate-restore`` can replay it backwards.

Symlinks are DIRECTORY-ONLY and RELATIVE. A tree browsed from Windows through
``\\\\wsl.localhost`` follows directory symlinks but not file symlinks (measured
2026-11-16), and an absolute link breaks once the tree is mirrored to another
host. Both kinds are refused.

Scope: moves on one filesystem tree. No git calls. A git work tree gets a hint
that history only follows once the result is committed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from ._reclaim import _same_filesystem

_MANIFEST_SUBDIR = "~/.scitex/scitex-storage/runtime/relocate-manifests"

#: Never descended into, never moved implicitly.
DEFAULT_SKIP = (".git", ".worktrees", ".venv", ".old")

#: Editor/OS leftovers: Office lock files, Windows download tags, temp/backup copies.
DEFAULT_JUNK_PREFIXES = ("~$", ".~lock")
DEFAULT_JUNK_SUFFIXES = (":Zone.Identifier", ".tmp", ".bak")


class RelocateConflict(Exception):
    """The plan would overwrite different content; nothing was changed."""


@dataclass
class Op:
    """One operation, recorded with tree-relative paths AS THEY ARE at that step."""

    kind: str  # "move" | "archive" | "link"
    src: str  # move: from; archive: the path archived; link: the link path
    dst: str  # move: to; archive: path inside the archive; link: relative target
    reason: str = ""  # archive: "junk" | "drop" | "duplicate"


@dataclass
class RelocatePlan:
    root: str
    run_id: str
    archive_root: str
    ops: list[Op] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    is_git_worktree: bool = False

    def count(self, kind: str, reason: str | None = None) -> int:
        return sum(
            1
            for o in self.ops
            if o.kind == kind and (reason is None or o.reason == reason)
        )


@dataclass
class RelocateManifest:
    run_id: str
    root: str
    archive_root: str
    applied_at: float
    ops: list[Op]
    complete: bool
    restored: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RelocateManifest:
        return cls(
            run_id=d["run_id"],
            root=d["root"],
            archive_root=d["archive_root"],
            applied_at=d["applied_at"],
            ops=[Op(**o) for o in d["ops"]],
            complete=d["complete"],
            restored=d.get("restored", False),
        )


# ----------------------------------------------------------------- plan file


def load_plan_file(path: str | Path) -> dict:
    """Read a YAML (or JSON) plan and check its shape, failing loud."""
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"plan must be a mapping, got {type(data).__name__}")
    unknown = set(data) - {"moves", "drop", "junk", "links", "skip"}
    if unknown:
        raise ValueError(f"unknown plan keys: {sorted(unknown)}")
    for i, m in enumerate(data.get("moves") or []):
        if not isinstance(m, dict) or set(m) != {"from", "to"}:
            raise ValueError(f"moves[{i}] must be {{from, to}}, got {m!r}")
    for i, ln in enumerate(data.get("links") or []):
        if not isinstance(ln, dict) or set(ln) != {"path", "target"}:
            raise ValueError(f"links[{i}] must be {{path, target}}, got {ln!r}")
    return data


def _norm(rel: str) -> str:
    raw = rel.strip()
    p = PurePosixPath(raw.rstrip("/"))
    if raw.startswith(("/", "~")) or p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"plan paths must be relative and inside root: {rel!r}")
    return str(p)


# ------------------------------------------------------------- virtual tree


class _VTree:
    """Index of the tree: virtual path -> ORIGINAL real path (files and dirs)."""

    def __init__(self, root: Path, skip: tuple[str, ...]):
        self.root = root
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_dir = os.path.relpath(dirpath, root)
            rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            keep = []
            for d in dirnames:
                rd = f"{rel_dir}/{d}" if rel_dir else d
                if not rel_dir and d in skip:
                    continue
                if (Path(dirpath) / d).is_symlink():
                    self.files[rd] = rd  # a dir symlink is a leaf, never descended
                    continue
                self.dirs.add(rd)
                keep.append(d)
            dirnames[:] = keep
            for f in filenames:
                rf = f"{rel_dir}/{f}" if rel_dir else f
                self.files[rf] = rf

    def is_file(self, p: str) -> bool:
        return p in self.files

    def is_dir(self, p: str) -> bool:
        return p in self.dirs

    def exists(self, p: str) -> bool:
        return self.is_file(p) or self.is_dir(p)

    def children(self, d: str) -> list[str]:
        pre = d + "/"
        names = {
            k[len(pre) :].split("/", 1)[0]
            for k in (*self.files, *self.dirs)
            if k.startswith(pre)
        }
        return sorted(names)

    def move(self, src: str, dst: str) -> None:
        """Re-key everything at or under ``src`` to ``dst`` (and create dst's parents)."""
        pre = src + "/"
        for table in (self.files,):
            for k in [k for k in table if k == src or k.startswith(pre)]:
                table[dst + k[len(src) :]] = table.pop(k)
        moved = {k for k in self.dirs if k == src or k.startswith(pre)}
        self.dirs -= moved
        self.dirs |= {dst + k[len(src) :] for k in moved}
        self._mkparents(dst)

    def remove(self, p: str) -> None:
        pre = p + "/"
        for k in [k for k in self.files if k == p or k.startswith(pre)]:
            del self.files[k]
        self.dirs = {k for k in self.dirs if not (k == p or k.startswith(pre))}

    def _mkparents(self, p: str) -> None:
        parts = p.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            self.dirs.add("/".join(parts[:i]))

    def origin(self, p: str) -> Path:
        return self.root / self.files[p]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same_content(a: Path, b: Path) -> bool:
    if a.is_symlink() or b.is_symlink():
        return a.is_symlink() and b.is_symlink() and os.readlink(a) == os.readlink(b)
    return a.stat().st_size == b.stat().st_size and _sha256(a) == _sha256(b)


# ------------------------------------------------------------------ planning


def plan_relocate(
    plan: dict,
    root: str | Path,
    *,
    run_id: str,
    archive_root: str | Path | None = None,
) -> RelocatePlan:
    """Simulate ``plan`` against ``root`` in order. Read-only; never mutates."""
    root_p = Path(root).expanduser().resolve()
    if not root_p.is_dir():
        raise FileNotFoundError(f"root is not a directory: {root_p}")
    arch = (
        Path(archive_root).expanduser()
        if archive_root
        else root_p / ".old" / "relocate"
    )
    skip = tuple(plan.get("skip") or ()) + DEFAULT_SKIP
    out = RelocatePlan(
        root=str(root_p),
        run_id=run_id,
        archive_root=str(arch),
        is_git_worktree=(root_p / ".git").exists(),
    )
    vt = _VTree(root_p, skip)

    def archive(p: str, reason: str) -> None:
        out.ops.append(Op("archive", p, f"{run_id}/{p}", reason))
        vt.remove(p)

    # 1. junk, anywhere
    junk = plan.get("junk") or {}
    prefixes = tuple(junk.get("prefixes", DEFAULT_JUNK_PREFIXES))
    suffixes = tuple(junk.get("suffixes", DEFAULT_JUNK_SUFFIXES))
    for f in sorted(vt.files):
        name = f.rsplit("/", 1)[-1]
        if name.startswith(prefixes) or name.endswith(suffixes):
            archive(f, "junk")

    # 2. moves, in order, merging into existing directories
    def merge(src: str, dst: str) -> None:
        parts = dst.split("/")
        blocked = next(
            (
                "/".join(parts[:i])
                for i in range(1, len(parts))
                if vt.is_file("/".join(parts[:i]))
            ),
            None,
        )
        if blocked:
            out.conflicts.append(f"{src} -> {dst} (a file occupies {blocked})")
            return
        if not vt.exists(dst):
            out.ops.append(Op("move", src, dst))
            vt.move(src, dst)
            return
        if vt.is_dir(src) and vt.is_dir(dst):
            for name in vt.children(src):
                merge(f"{src}/{name}", f"{dst}/{name}")
            if not vt.children(src):
                vt.dirs.discard(src)  # emptied by the merge; removed on apply
            return
        if (
            vt.is_file(src)
            and vt.is_file(dst)
            and _same_content(vt.origin(src), vt.origin(dst))
        ):
            archive(src, "duplicate")
            return
        out.conflicts.append(f"{src} -> {dst}")

    for m in plan.get("moves") or []:
        src, dst = _norm(m["from"]), _norm(m["to"])
        if dst == src or dst.startswith(src + "/"):
            raise ValueError(f"cannot move a path into itself: {src} -> {dst}")
        if not vt.exists(src):
            continue  # already moved by an earlier step, or absent: nothing to do
        merge(src, dst)

    # 3. drops (evaluated on the tree as the moves left it)
    for raw in plan.get("drop") or []:
        p = _norm(raw)
        if vt.exists(p):
            archive(p, "drop")

    # 4. directory links (relative only)
    for ln in plan.get("links") or []:
        path, target = _norm(ln["path"]), _norm(ln["target"])
        if not vt.is_dir(target):
            raise ValueError(
                f"link target must be an existing directory after the moves: {target} "
                "(file links are refused: Windows Explorer cannot open them via \\\\wsl.localhost)"
            )
        if vt.exists(path):
            out.conflicts.append(f"link path already exists: {path}")
            continue
        rel = os.path.relpath(target, str(PurePosixPath(path).parent) or ".")
        out.ops.append(Op("link", path, rel))
        vt.files[path] = path
    return out


# ------------------------------------------------------------------- applying


def _manifest_dir() -> Path:
    return Path(_MANIFEST_SUBDIR).expanduser()


def _write_manifest(m: RelocateManifest) -> Path:
    p = _manifest_dir() / f"{m.run_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(m.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return p


def _move(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(f"refusing to overwrite {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _same_filesystem(src, dst.parent):
        os.rename(src, dst)  # atomic, copies nothing
    else:
        _rsync_move(src, dst)


def _rsync_move(src: Path, dst: Path) -> None:
    """Cross-filesystem move via rsync: resumable, checksummed, source removed last.

    ``--partial`` keeps interrupted files so a rerun resumes instead of restarting;
    ``--checksum`` compares content, not mtime/size. rsync removes each source file
    only after it is safely written (``--remove-source-files``). The emptied source
    directories are pruned afterwards. A failure raises, and the source keeps
    every file not yet transferred.
    """
    import subprocess

    if shutil.which("rsync") is None:
        raise RuntimeError("rsync is required for a cross-filesystem move")
    is_dir = src.is_dir() and not src.is_symlink()
    s = f"{src}/" if is_dir else str(src)
    d = f"{dst}/" if is_dir else str(dst)
    subprocess.run(
        ["rsync", "-a", "--partial", "--checksum", "--remove-source-files", s, d],
        check=True,
    )
    if is_dir:
        for dirpath, _dirs, _files in sorted(os.walk(src), key=lambda t: -len(t[0])):
            if not os.listdir(dirpath):
                os.rmdir(dirpath)


def _prune_empty_parents(path: Path, stop: Path) -> None:
    p = path.parent
    while p != stop and p.is_dir() and not any(p.iterdir()):
        p.rmdir()
        p = p.parent


def apply_relocate(plan: RelocatePlan) -> RelocateManifest:
    """Execute a conflict-free plan in order. The manifest is written even on failure."""
    if plan.conflicts:
        raise RelocateConflict(f"{len(plan.conflicts)} conflict(s); nothing changed")
    import time

    root, arch = Path(plan.root), Path(plan.archive_root)
    done: list[Op] = []
    manifest = RelocateManifest(
        plan.run_id, plan.root, plan.archive_root, time.time(), done, False
    )
    try:
        for op in plan.ops:
            if op.kind == "move":
                src, dst = root / op.src, root / op.dst
                _move(src, dst)
                _prune_empty_parents(src, root)
            elif op.kind == "archive":
                _move(root / op.src, arch / op.dst)
                _prune_empty_parents(root / op.src, root)
            elif op.kind == "link":
                lp = root / op.src
                lp.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(op.dst, lp, target_is_directory=True)
            done.append(op)
        manifest.complete = True
    finally:
        _write_manifest(manifest)
    return manifest


def load_manifest(run_id: str) -> RelocateManifest:
    p = _manifest_dir() / f"{run_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"no relocate manifest for run {run_id!r} at {p}")
    return RelocateManifest.from_dict(json.loads(p.read_text(encoding="utf-8")))


def restore_relocate(run_id: str) -> RelocateManifest:
    """Replay a run backwards: unlink links, move back, un-archive. Never overwrites."""
    m = load_manifest(run_id)
    if m.restored:
        raise ValueError(f"run {run_id} was already restored")
    root, arch = Path(m.root), Path(m.archive_root)
    for op in reversed(m.ops):
        if op.kind == "link":
            lp = root / op.src
            if lp.is_symlink() and os.readlink(lp) == op.dst:
                lp.unlink()
                _prune_empty_parents(lp, root)
        elif op.kind == "move":
            _move(root / op.dst, root / op.src)
            _prune_empty_parents(root / op.dst, root)
        elif op.kind == "archive":
            _move(arch / op.dst, root / op.src)
    m.restored = True
    _write_manifest(m)
    return m


# EOF

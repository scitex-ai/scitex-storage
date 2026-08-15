#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inode-aware sweep: tar-in-place rotation for many-small-files directories.

The MVP verb behind ``scitex-storage sweep`` — the Spartan/GPFS-inode-crisis
counterpart to ``images prune``. A directory with many small files consumes
one inode per file; tarring it collapses that to one inode (the tar itself),
with zero content loss (``tar xf`` recovers everything).

Design constraints, informed directly by scitex-hpc (Spartan execution
owner) reviewing this design before it was built:

* **Compute-node-only, enforced in code.** Building a tar reads file
  *content* (unlike ``scan`` / ``images prune``, which are stat-only) and
  ``shutil.rmtree`` is a heavy metadata op — both are exactly what Spartan's
  login-node hook (``deny_heavy_spartan_login.sh``) exists to keep off login
  nodes. :func:`apply_sweep` asserts ``$SLURM_JOB_ID`` is set and refuses to
  run otherwise, rather than trusting the caller to remember.
* **Pure Python, never shells out to ``tar``/``find``.** Matches how `scan`
  dodges the login-node hook's naive command-pattern matching (it never
  fires on shelled-out find/du in the first place, since there aren't any).
* **Explicit per-name consent, never a blanket threshold-apply.**
  :func:`plan_sweep` auto-discovers candidates by size, but
  :func:`apply_sweep` only ever touches directories named in an explicit
  ``confirm_names`` list a human reviewed — directories under a shared
  project fileset aren't uniformly one owner's to sweep unilaterally.
* **Freshness exclusion.** A candidate whose most-recently-modified file is
  younger than ``min_age_seconds`` is excluded — protects a directory that's
  actually a live job's working/output tree, not an inert hog.
* **One candidate at a time, walltime-aware.** :func:`apply_sweep` checks
  remaining SLURM walltime before starting each candidate and stops
  gracefully (never starts a candidate it likely can't finish) rather than
  risking a mid-tar kill. Each candidate's own tar-build/verify/rename/
  delete is otherwise atomic — a kill mid-candidate leaves the original
  directory untouched (verification only happens against a *temp* tar name;
  the original is only removed after a successful rename to the final name).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .._measure._scan import scan

DEFAULT_MIN_AGE_SECONDS = 24 * 60 * 60  # 24h


class InsufficientSpaceError(RuntimeError):
    """Raised when the target filesystem cannot hold the tar being built.

    Its own error class rather than a bare ``RuntimeError`` so a caller can
    distinguish "not enough room" -- recoverable, and the operator can free
    space and retry -- from a genuine failure mid-write.
    """


@dataclass
class SweepCandidate:
    """One immediate child of the scanned directory that meets the threshold."""

    name: str
    path: Path
    file_count: int  # at plan time
    size: int
    newest_mtime: float


#: Fraction of the artifact size that must ALSO be free after writing it.
#: A tar built to the exact byte leaves a filesystem at 0 bytes free, which
#: breaks every other writer on it -- including, on this fleet, the SQLite
#: card board that every agent writes to. Headroom is not politeness.
SPACE_MARGIN = 0.05


@dataclass(frozen=True)
class SpaceVerdict:
    """Whether an artifact of ``needed`` bytes may be written. Three-state.

    ``ok is None`` means the question could not be ANSWERED (the
    destination could not be stat'd) -- distinct from ``ok is False``
    ("answered: no room"). Collapsing the two is how a cleanup tool
    proceeds on a filesystem it never actually measured.
    """

    ok: bool | None
    needed: int | None
    available: int | None
    detail: str


def check_space(needed: int | None, available: int | None) -> SpaceVerdict:
    """Compare an artifact estimate against real free space on the target.

    Both halves are required and they fail differently:

    * an estimate with NO destination probe passes on a full disk -- the
      exact defect that let ``sweep`` write its tar beside the source and
      threaten to fill the very filesystem it was invoked to relieve;
    * a destination probe with NO estimate cannot say whether what is
      free is *enough*.

    So a one-sided check is theatre. ``None`` on either side yields
    ``ok=None`` -- unknown, never an optimistic pass.
    """
    if needed is None or available is None:
        missing = "artifact size" if needed is None else "destination free space"
        return SpaceVerdict(
            ok=None,
            needed=needed,
            available=available,
            detail=f"could not determine {missing} -- refusing to guess",
        )
    required = int(needed * (1.0 + SPACE_MARGIN))
    if available < required:
        short = required - available
        return SpaceVerdict(
            ok=False,
            needed=required,
            available=available,
            detail=(
                f"destination is short by {short} bytes: needs {required} "
                f"(artifact {needed} + {int(SPACE_MARGIN * 100)}% headroom), "
                f"has {available}"
            ),
        )
    return SpaceVerdict(
        ok=True,
        needed=required,
        available=available,
        detail=f"destination has {available} bytes, needs {required}",
    )


def free_bytes(path: str | Path) -> int | None:
    """Bytes available to an unprivileged writer at ``path``, or ``None``.

    Uses ``f_bavail`` (what a normal user may actually use), not
    ``f_bfree`` (which counts root-reserved blocks a sweep cannot have).
    Returns ``None`` rather than raising or guessing when the path cannot
    be stat'd -- that is a could-not-look, and :func:`check_space` turns
    it into an unknown rather than a pass.
    """
    try:
        st = os.statvfs(os.fspath(path))
    except OSError:
        return None
    return st.f_bavail * st.f_frsize


@dataclass
class SweepPlan:
    """The result of :func:`plan_sweep` — never touches the filesystem."""

    directory: Path
    threshold_files: int
    min_age_seconds: float
    candidates: list[SweepCandidate] = field(default_factory=list)
    skipped_fresh: list[SweepCandidate] = field(default_factory=list)

    @property
    def reclaimable_inodes(self) -> int:
        """Estimated inodes reclaimed if every candidate were swept."""
        return sum(max(0, c.file_count - 1) for c in self.candidates)


def plan_sweep(
    directory: str | Path,
    threshold_files: int,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    max_depth: int | None = None,
) -> SweepPlan:
    """Discover sweep candidates under ``directory`` — read-only.

    Reuses :func:`~scitex_storage._measure._scan.scan` (one walk, no duplicate
    traversal). A child qualifies as a candidate if it is a directory with
    ``file_count >= threshold_files`` AND its newest file is at least
    ``min_age_seconds`` old (excluded into ``skipped_fresh`` otherwise, to
    protect a directory that's still actively being written to).

    Fail-loud on a missing / non-directory ``directory`` (via ``scan``).
    """
    result = scan(directory, max_depth=max_depth)
    now = time.time()

    candidates: list[SweepCandidate] = []
    skipped_fresh: list[SweepCandidate] = []
    for c in result.children:
        if not c.is_dir or c.file_count < threshold_files:
            continue
        cand = SweepCandidate(
            name=c.name,
            path=c.path,
            file_count=c.file_count,
            size=c.size,
            newest_mtime=c.newest_mtime,
        )
        if now - c.newest_mtime < min_age_seconds:
            skipped_fresh.append(cand)
        else:
            candidates.append(cand)

    return SweepPlan(
        directory=result.root,
        threshold_files=threshold_files,
        min_age_seconds=min_age_seconds,
        candidates=candidates,
        skipped_fresh=skipped_fresh,
    )


@dataclass
class SweptCandidate:
    """One candidate :func:`apply_sweep` actually tarred and removed."""

    candidate: SweepCandidate
    tar_path: Path
    tar_size: int
    member_count: int  # files actually written into the tar (source of truth)
    reclaimed_inodes: int


@dataclass
class SweepResult:
    """The result of :func:`apply_sweep`."""

    swept: list[SweptCandidate] = field(default_factory=list)
    stopped_low_walltime: list[SweepCandidate] = field(default_factory=list)

    @property
    def reclaimed_inodes(self) -> int:
        return sum(s.reclaimed_inodes for s in self.swept)


def _require_slurm_job() -> str:
    """Fail loud unless running inside a SLURM allocation.

    Hard requirement (scitex-hpc, 2026-07-11): tar reads file content and
    rmtree is a heavy metadata op, both barred from Spartan login nodes.
    Asserted in code rather than trusted to the caller/docs.
    """
    job_id = os.environ.get("SLURM_JOB_ID")
    if not job_id:
        raise RuntimeError(
            "apply_sweep must run inside a SLURM allocation (SLURM_JOB_ID is "
            "not set) -- tar reads file content and rmtree is a heavy "
            "metadata op, both barred from Spartan login nodes. Submit via "
            "sbatch, or use srun --overlap --jobid=<held-allocation>."
        )
    return job_id


def _parse_slurm_remaining(text: str) -> float | None:
    """Parse SLURM's ``squeue -o %L`` remaining-time format into seconds.

    Accepts ``D-HH:MM:SS`` / ``HH:MM:SS`` / ``MM:SS``; returns ``None`` for
    ``UNLIMITED`` / ``INVALID`` / unparseable text (treated as "unknown" by
    the caller, not an error).
    """
    text = text.strip()
    if not text or text.upper() in ("UNLIMITED", "INVALID", "N/A"):
        return None
    days = 0
    if "-" in text:
        day_part, text = text.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    if not parts or len(parts) > 3:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _remaining_walltime_seconds() -> float | None:
    """Best-effort remaining walltime in the current SLURM allocation.

    Tries ``$SLURM_JOB_END_TIME`` (epoch seconds) first, else shells out to
    ``squeue -h -j $SLURM_JOB_ID -o %L``. Returns ``None`` (unknown) rather
    than raising when neither source is available -- this is a scheduling
    nicety on top of the real safety gates (`_require_slurm_job` + one-
    candidate-at-a-time + verify-before-delete), so "can't tell" degrades to
    "proceed" rather than blocking a legitimate sweep.
    """
    end_time = os.environ.get("SLURM_JOB_END_TIME")
    if end_time:
        try:
            return float(end_time) - time.time()
        except ValueError:
            pass

    job_id = os.environ.get("SLURM_JOB_ID")
    if not job_id:
        return None
    try:
        proc = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%L"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _parse_slurm_remaining(proc.stdout)


def _sweep_one(candidate: SweepCandidate) -> SweptCandidate:
    """Tar ``candidate.path`` in place, verify, then remove the original.

    Single directory walk (builds the tar and counts members together —
    no redundant re-scan, since GPFS metadata ops are slow at high inode
    utilization). Writes to a temp name first; only renames to the final
    ``<name>.tar`` (and only then removes the original) once the tar is
    confirmed non-empty. A crash mid-write leaves the temp file and the
    original directory both present -- never a data-loss state.
    """
    tar_path = candidate.path.parent / f"{candidate.name}.tar"
    if tar_path.exists():
        raise FileExistsError(
            f"refusing to sweep {candidate.path}: {tar_path} already exists"
        )

    # PREFLIGHT. The tar is written BESIDE the source, so it consumes the
    # same filesystem the sweep was invoked to relieve. Without this check
    # the verb is INVERTED: it does maximum damage exactly where it is most
    # needed, driving a nearly-full filesystem to zero and failing partway,
    # having consumed the last free space and accomplished nothing. On this
    # fleet that filesystem also carries the card board every agent writes.
    # A clean refusal naming the shortfall beats a half-written tar on a
    # dead disk.
    verdict = check_space(candidate.size, free_bytes(candidate.path.parent))
    if verdict.ok is not True:
        raise InsufficientSpaceError(
            f"refusing to sweep {candidate.path}: {verdict.detail}. "
            f"The tar would be written to {candidate.path.parent}, the same "
            f"filesystem as the source. Free space there first, or sweep a "
            f"smaller candidate."
        )
    tmp_path = candidate.path.parent / f".{candidate.name}.tar.sweeping"
    if tmp_path.exists():
        tmp_path.unlink()  # leftover from a prior interrupted attempt

    member_count = 0
    try:
        with tarfile.open(tmp_path, "w") as tar:
            for dirpath, dirnames, filenames in os.walk(
                candidate.path, topdown=True, followlinks=False
            ):
                # Never descend into symlinked directories (matches scan()'s doctrine).
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not os.path.islink(os.path.join(dirpath, d))
                ]
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    arcname = os.path.join(
                        candidate.name, os.path.relpath(fpath, candidate.path)
                    )
                    tar.add(fpath, arcname=arcname, recursive=False)
                    member_count += 1
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    if member_count == 0:
        tmp_path.unlink()
        raise RuntimeError(
            f"refusing to sweep {candidate.path}: tar ended up with 0 "
            "members (directory changed since planning?) -- leaving the "
            "original untouched"
        )

    tmp_path.rename(tar_path)
    shutil.rmtree(candidate.path)

    return SweptCandidate(
        candidate=candidate,
        tar_path=tar_path,
        tar_size=tar_path.stat().st_size,
        member_count=member_count,
        reclaimed_inodes=max(0, member_count - 1),
    )


def apply_sweep(
    plan: SweepPlan,
    confirm_names: list[str],
    min_remaining_seconds: float = 300.0,
) -> SweepResult:
    """Sweep exactly the ``confirm_names`` subset of ``plan.candidates``.

    ``confirm_names`` is an explicit allowlist a human reviewed — this is
    never "apply the whole plan"; every name must be spelled out, and any
    name not found among ``plan.candidates`` raises (typo protection, and
    protection against a stale plan). Candidates in the plan but not named
    in ``confirm_names`` are simply left alone, not an error.

    Refuses to run outside a SLURM allocation (see `_require_slurm_job`).
    Processes one candidate at a time, checking remaining walltime before
    each — candidates it likely can't finish are reported in
    ``stopped_low_walltime`` rather than started.
    """
    _require_slurm_job()

    by_name = {c.name: c for c in plan.candidates}
    unknown = sorted(n for n in confirm_names if n not in by_name)
    if unknown:
        raise ValueError(
            f"--confirm named {unknown!r} which is not in the plan's "
            f"candidates ({sorted(by_name)}) -- typo, or the plan is stale "
            "(re-run without --apply first to see current candidates)"
        )

    to_sweep = [by_name[n] for n in confirm_names]
    swept: list[SweptCandidate] = []
    stopped: list[SweepCandidate] = []
    for i, candidate in enumerate(to_sweep):
        remaining = _remaining_walltime_seconds()
        if remaining is not None and remaining < min_remaining_seconds:
            stopped.extend(to_sweep[i:])
            break
        swept.append(_sweep_one(candidate))

    return SweepResult(swept=swept, stopped_low_walltime=stopped)


@dataclass
class SweptEntry:
    """One already-swept directory found by :func:`sweep_status`."""

    name: str
    tar_path: Path
    tar_size: int
    original_still_present: bool  # anomaly flag: sweep didn't clean up, or name collision


def sweep_status(directory: str | Path) -> list[SweptEntry]:
    """List directories under ``directory`` that have already been swept.

    Read-only. A child is "swept" if a sibling ``<name>.tar`` file exists
    directly in ``directory``. Flags the anomalous case where the original
    directory of the same name still exists alongside its tar (an
    interrupted sweep, or an unrelated tar coincidentally sharing a name).

    Fail-loud on a missing / non-directory ``directory``.
    """
    directory = Path(directory).expanduser()
    if not directory.exists():
        raise FileNotFoundError(f"path does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"not a directory: {directory}")
    directory = directory.resolve()

    entries: list[SweptEntry] = []
    with os.scandir(directory) as it:
        scanned = sorted(it, key=lambda e: e.name)
    for entry in scanned:
        if entry.is_symlink() or not entry.name.endswith(".tar"):
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        name = entry.name[: -len(".tar")]
        original = directory / name
        entries.append(
            SweptEntry(
                name=name,
                tar_path=Path(entry.path),
                tar_size=entry.stat(follow_symlinks=False).st_size,
                original_still_present=(
                    original.is_dir() and not original.is_symlink()
                ),
            )
        )
    return entries


# EOF

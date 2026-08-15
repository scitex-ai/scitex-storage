#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inode (file-count) capacity probe for the filesystem backing a path.

The verb behind ``scitex-storage inodes``. Answers one question, honestly:
*how close is this filesystem to running out of inodes?*

WHY THIS EXISTS, and why it is deliberately the smallest thing in the
package: inode exhaustion is invisible until it is fatal. A filesystem
with terabytes free but no free inodes fails EVERY write, and the jobs
that die do not say "out of inodes" — they say whatever their own error
handling happens to say, which is usually nothing useful. A Spartan
project (~6.6M files) hit 95%+ inodes this week while showing plenty of
free space. Nobody saw it coming because nobody was looking, and nobody
was looking because looking was hard.

So the design constraint that drives everything here is: **this probe
must work when nothing else does.**

* **No system dependencies.** Unlike ``scan`` (which needs ``fd``), this
  module shells out to nothing and imports nothing outside the standard
  library. ``os.statvfs`` is a single syscall.
* **No login shell, no modules, no PATH.** Spartan's ``check_project_usage``
  is a *login-shell function*; it broke this week precisely when a login
  shell broke — i.e. a probe that only works when everything else already
  works, which is when you least need it. This one is importable Python
  and runs from a bare ``srun`` step, a cron line, or a container.
* **No walk.** Cost is O(1) per path, not O(files). Walking a
  multi-million-file tree to count inodes on an inode-starved filesystem
  is self-defeating: it is slow exactly when metadata ops are slow.

THREE-STATE VERDICTS — the load-bearing design decision. A probe must
never conflate "I looked and it is fine" with "I could not look". The
distinction is not pedantry; downstream this number authorises deleting
things, and a tool that reports 0% because it could not read is a tool
that authorises deleting on no evidence. So :class:`InodeUsage` carries
an explicit :attr:`InodeUsage.verdict`:

* ``MEASURED`` — real numbers from a real inode table.
* ``NOT_APPLICABLE`` — the filesystem genuinely has no fixed inode table
  and cannot run out. btrfs, ZFS and friends allocate inodes dynamically
  and report ``f_files == 0`` through ``statvfs``. This is the case a
  naive implementation gets catastrophically wrong: ``0 - 0 == 0`` used
  of ``0`` total renders as a reassuring "0%" (or a ZeroDivisionError),
  when the truth is "this question does not apply here". Reporting 0% for
  a filesystem you did not measure is a lie that looks like good news.
* ``COULD_NOT_LOOK`` — the path is gone, unreadable, or the mount is
  wedged (a stale NFS handle raises here). Never silently 0.

SCOPE — what the denominator actually is. ``statvfs`` reports whatever
the MOUNT backing the path chooses to expose, and that is not always the
underlying filesystem. This matters more than it sounds, and it was worth
measuring rather than assuming:

* On an ordinary filesystem, ``f_files`` is the filesystem's own inode
  table — e.g. Spartan's ``/home`` reports 733,761,971, an unrounded
  hardware-derived figure.
* On a GPFS **independent fileset** — which is how HPC per-project
  directories are usually carved out — ``f_files`` is the FILESET'S OWN
  QUOTA. Verified on Spartan 2026-07-17: ``/data/gpfs/projects/punim0264``
  reports exactly 7,000,000 and ``punim2354`` exactly 8,000,000. Round
  numbers are quotas, not hardware. Cross-checked the same minute against
  Spartan's own ``check_project_usage``, which reported 6,731,076 used
  where this reported 6,731,073 — a three-inode drift between two calls,
  i.e. the same number.

So on an HPC project path this probe answers the question that actually
kills jobs (the project quota), with no ``mmlsquota``, no module load and
no login shell. That is a happy accident of how GPFS reports filesets, NOT
a guarantee this module enforces — a site could well mount projects some
other way. Hence :attr:`InodeUsage.mount` is always reported alongside the
numbers: rather than promise a denominator it cannot control, this module
shows you which mount the figure came from, so a number attributed to the
wrong thing is visible rather than implied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Real numbers from a real inode table.
MEASURED = "measured"
#: The filesystem allocates inodes dynamically and cannot run out (btrfs/ZFS).
NOT_APPLICABLE = "not-applicable"
#: The path could not be stat-ed. NEVER to be treated as "fine".
COULD_NOT_LOOK = "could-not-look"

#: Default percentage at which :func:`probe` flags a filesystem as critical.
#: 90 is not arbitrary: the failure is a cliff, not a slope — at 100% every
#: write fails — and reclaiming inodes takes time (you must find, verify and
#: archive millions of small files), so the alarm has to fire while there is
#: still room to act. punim0264 was at 89% a week before it hit 95%+.
DEFAULT_WARN_PERCENT = 90.0


@dataclass
class InodeUsage:
    """Inode capacity for the mount backing one path.

    ``total`` / ``used`` / ``free`` / ``percent_used`` are ``None`` unless
    :attr:`verdict` is :data:`MEASURED` — there is deliberately no "0"
    default, so that an unmeasured filesystem cannot be mistaken for an
    empty one by a caller that forgets to check the verdict.
    """

    path: Path
    verdict: str
    mount: str | None = None
    fstype: str | None = None
    total: int | None = None
    used: int | None = None
    free: int | None = None
    percent_used: float | None = None
    detail: str | None = None  # why, whenever verdict is not MEASURED

    @property
    def is_critical(self) -> bool:
        """True only when MEASURED and at/over :data:`DEFAULT_WARN_PERCENT`.

        Deliberately False for :data:`COULD_NOT_LOOK`: an unknown is not an
        alarm, it is an unknown, and callers must handle it as its own case
        (see :func:`probe_paths` and the CLI's exit codes) rather than have
        it silently fold into either "fine" or "critical".
        """
        return self.percent_used is not None and self.percent_used >= DEFAULT_WARN_PERCENT

    def exceeds(self, warn_percent: float) -> bool:
        """True when MEASURED and at/over ``warn_percent``."""
        return self.percent_used is not None and self.percent_used >= warn_percent


def _mount_table() -> list[tuple[str, str]]:
    """Return ``[(mount_point, fstype)]`` from ``/proc/self/mountinfo``.

    Linux-only by nature. Returns ``[]`` on any platform or container that
    does not expose it (macOS, for instance) — the mount/fstype annotation
    is a nicety, and its absence must never stop the actual measurement,
    which comes from ``statvfs`` and works everywhere.
    """
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError:
        return []

    table: list[tuple[str, str]] = []
    for line in raw.splitlines():
        # mountinfo: <id> <parent> <maj:min> <root> <mount-point> <opts>...
        #            - <fstype> <source> <super-opts>
        # The optional-fields run before " - " is variable-length, which is
        # exactly why the separator exists; split on it rather than guessing
        # field offsets.
        parts = line.split(" - ")
        if len(parts) != 2:
            continue
        left, right = parts[0].split(), parts[1].split()
        if len(left) < 5 or not right:
            continue
        # Mount points are octal-escaped (a space is \040).
        mount_point = left[4].encode().decode("unicode_escape")
        table.append((mount_point, right[0]))
    return table


def _mount_for(path: Path, table: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """Return ``(mount_point, fstype)`` for the longest mount prefix of ``path``.

    Longest-prefix, not first-match: ``/`` is a prefix of everything, so a
    first-match scan would report every path as living on the root
    filesystem and quietly attribute a full ``/home`` to ``/``.
    """
    best: tuple[str, str] | None = None
    for mount_point, fstype in table:
        try:
            candidate = Path(mount_point)
        except (ValueError, OSError):
            continue
        if path == candidate or candidate in path.parents:
            if best is None or len(mount_point) > len(best[0]):
                best = (mount_point, fstype)
    return best if best is not None else (None, None)


def usage_from_counts(
    path: str | Path,
    total: int,
    free: int,
    *,
    mount: str | None = None,
    fstype: str | None = None,
) -> InodeUsage:
    """Build an :class:`InodeUsage` from raw inode counts. Pure — no I/O.

    This is the whole decision layer, deliberately separated from the
    syscall that feeds it. Two reasons, and the second is the one that
    matters:

    1. It is directly testable with plain integers. The interesting
       behaviour here is arithmetic and classification, not a syscall, so
       nothing has to be faked to exercise it.
    2. Counts do not only come from a local ``statvfs``. Probing a remote
       host over ssh yields a bare ``(f_files, f_ffree)`` pair with no
       local path to stat, and a quota backend would yield the same shape.
       All of them deserve the identical verdict logic rather than a
       reimplementation that drifts.

    ``total == 0`` means the filesystem has no fixed inode table (btrfs
    and ZFS allocate on demand) — reported as :data:`NOT_APPLICABLE`, never
    as ``0%``.
    """
    p = Path(path).expanduser() if not isinstance(path, Path) else path

    if total == 0:
        return InodeUsage(
            path=p,
            verdict=NOT_APPLICABLE,
            mount=mount,
            fstype=fstype,
            detail=(
                "filesystem reports no fixed inode table (statvfs f_files=0); "
                "inodes are allocated dynamically and cannot be exhausted"
            ),
        )

    used = total - free
    return InodeUsage(
        path=p,
        verdict=MEASURED,
        mount=mount,
        fstype=fstype,
        total=total,
        used=used,
        free=free,
        percent_used=(used / total) * 100.0,
    )


def probe(path: str | Path) -> InodeUsage:
    """Report inode capacity for the mount backing ``path``.

    The I/O half: one ``statvfs``, plus the mount annotation. All
    classification is delegated to :func:`usage_from_counts`.

    Never raises for an unreadable/missing path — that is a
    :data:`COULD_NOT_LOOK` verdict, because a probe whose job is to report
    the state of a possibly-broken system must be able to report "broken"
    rather than become broken itself. Genuine programming errors (a
    non-path argument) still raise.
    """
    p = Path(path).expanduser()

    try:
        st = os.statvfs(p)
    except OSError as exc:
        return InodeUsage(
            path=p,
            verdict=COULD_NOT_LOOK,
            detail=f"{exc.__class__.__name__}: {exc.strerror or exc}",
        )

    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    mount, fstype = _mount_for(resolved, _mount_table())

    return usage_from_counts(p, st.f_files, st.f_ffree, mount=mount, fstype=fstype)


def probe_paths(paths: list[str | Path]) -> list[InodeUsage]:
    """Probe several paths, returning one :class:`InodeUsage` each, in order.

    Deliberately deduplicates nothing: two paths on the same mount are a
    legitimate thing to ask about, and silently collapsing them would make
    the output stop corresponding to the question that was asked.
    """
    return [probe(p) for p in paths]


# EOF

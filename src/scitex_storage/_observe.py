#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_observe.py
"""Multi-host observation: read every host in the registry, including the broken ones.

Orchestrator for the observation layer. The host list comes from
scitex-dev's registry, which is the SSOT -- scitex-storage does not keep
a second list of machines. A parallel list drifts, and a storage
dashboard that silently omits a host is worse than no dashboard: the host
you forgot is exactly the one that fills up.

**An unreachable host is a ROW, never an absence.** If ssh fails, the
host still appears, rendered ``could-not-look``, with the reason -- the
incident corpus is survivor-biased, so whatever surfaces health must not
vanish with the thing it was watching.

Parsing/classification lives in :mod:`._observe_df`; per-host probing and
HPC project discovery in :mod:`._observe_hosts`. This module keeps the
transport (``subprocess_runner``), the registry-driven fleet sweep
(``observe_fleet``) and the snapshot cache the GUI reads, and re-exports
the split-out names so ``from scitex_storage._observe import ...`` is
unchanged.
"""

from __future__ import annotations

import os
from typing import Sequence

from ._fleet_status import COULD_NOT_LOOK, FleetSnapshot, HostStorage
from ._observe_df import (  # noqa: F401 -- re-exported public API
    DF_INODE_CMD,
    DF_SPACE_CMD,
    IMAGE_SOURCE_PREFIXES,
    PSEUDO_SOURCES,
    index_by_mount,
    is_structural,
    parse_df_posix,
    used_pct,
)
from ._observe_hosts import (  # noqa: F401 -- re-exported public API
    HPC_PROJECT_GROUP_PREFIXES,
    HPC_PROJECT_ROOT,
    ProbeOutcome,
    Runner,
    observe_host,
    observe_hpc_projects,
    parse_project_groups,
)

#: Roles whose relevant storage is project allocations discovered from
#: groups, not the global mount table. HPC login nodes report ~200
#: infrastructure mounts and none of the user's actual allocation.
_HPC_ROLES = ("hpc-login", "hpc", "hpc-compute")


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
def subprocess_runner(
    ssh_alias: str | None, timeout_seconds: float = 30.0
) -> Runner:
    """Build a Runner that shells out, locally or over ssh.

    ``ssh_alias=None`` means "this machine" and runs the command
    directly. A timeout is mandatory rather than optional: a NAS that
    accepts the TCP connection and then never answers would otherwise
    hang the whole fleet sweep behind one box, and a dashboard that
    never renders is indistinguishable from a dashboard that says
    everything is fine.
    """
    import shlex
    import subprocess

    def run(host: str, command: str) -> ProbeOutcome:
        argv = (
            shlex.split(command)
            if ssh_alias is None
            else [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={int(timeout_seconds)}",
                ssh_alias,
                command,
            ]
        )
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ProbeOutcome(
                ok=False, error=f"timed out after {timeout_seconds:.0f}s"
            )
        except OSError as exc:
            return ProbeOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")
        detail = (proc.stderr or "").strip().splitlines()
        if proc.returncode != 0:
            # PARTIAL SUCCESS IS STILL SUCCESS. `df` exits non-zero when
            # ANY single mount is unreadable -- one stale samba share on
            # nas1/nas2 made the whole host report could-not-look while
            # df had already printed every other filesystem to stdout.
            # Throwing that away turns "one mount is broken" into "this
            # entire NAS is unobservable", which is both wrong and the
            # more alarming of the two.
            if proc.stdout.strip():
                return ProbeOutcome(
                    ok=True,
                    stdout=proc.stdout,
                    error=f"exit {proc.returncode} (partial): "
                    f"{detail[-1] if detail else 'no stderr'}",
                )
            return ProbeOutcome(
                ok=False,
                error=f"exit {proc.returncode}: {detail[-1] if detail else 'no stderr'}",
            )
        return ProbeOutcome(ok=True, stdout=proc.stdout)

    return run


# --------------------------------------------------------------------------
# Fleet assembly
# --------------------------------------------------------------------------
def observe_fleet(
    keep_mounts_by_host: dict[str, Sequence[str]] | None = None,
    timeout_seconds: float = 30.0,
) -> list[HostStorage]:
    """Observe every host in the scitex-dev registry (the SSOT).

    scitex-storage keeps NO host list of its own. If a machine is missing
    here it is missing from the registry, and that is one place to fix
    rather than two places to drift.

    HPC hosts are observed by PROJECT ALLOCATION (discovered from the
    user's groups), not by the global mount table -- a login node's df is
    ~200 rows of cluster infrastructure and none of it is the user's
    storage. Every other host is observed by its mounts.

    A registry that cannot be read at all yields a single could-not-look
    row rather than an empty fleet -- "no hosts" and "could not ask" are
    different answers and only one of them is reassuring.
    """
    try:
        from scitex_dev.hosts import list_hosts

        records = list_hosts()
    except Exception as exc:  # registry absent/unreadable/unexpected shape
        return [
            HostStorage(
                host="(registry)",
                role="unknown",
                mount="(hosts.yaml)",
                verdict=COULD_NOT_LOOK,
                note=f"host registry unreadable: {type(exc).__name__}: {exc}",
            )
        ]

    rows: list[HostStorage] = []
    for rec in records:
        role = str(getattr(rec, "role", None) or getattr(rec, "kind", "") or "unknown")
        runner = subprocess_runner(rec.ssh_alias, timeout_seconds)
        if role in _HPC_ROLES:
            rows.extend(observe_hpc_projects(rec.name, role, runner))
        else:
            keep = (keep_mounts_by_host or {}).get(rec.name)
            rows.extend(observe_host(rec.name, role, runner, keep))
    return rows


# --------------------------------------------------------------------------
# Snapshot cache -- the GUI reads this, NEVER gathers live in a request
# --------------------------------------------------------------------------
#
# observe_fleet() ssh-probes six hosts and takes ~90s. Calling it inside a
# Django request handler would hang the page well past any client timeout
# -- the exact lesson _django/views.py already records about scan(). So the
# gather runs OUT OF BAND (a periodic job) and writes a rendered snapshot
# here; the view only ever reads this file.

def default_snapshot_path() -> str:
    """Where the rendered fleet dashboard is cached.

    Under the storage runtime dir, honouring ``SCITEX_DIR`` when set so it
    lands wherever the rest of scitex-storage's runtime state lives.
    """
    base = os.environ.get("SCITEX_DIR") or os.path.expanduser("~/.scitex")
    return os.path.join(base, "scitex-storage", "runtime", "fleet-dashboard.html")


def default_bubbles_path() -> str:
    """Where the rendered capacity-bubble page is cached (sibling of the table)."""
    base = os.environ.get("SCITEX_DIR") or os.path.expanduser("~/.scitex")
    return os.path.join(base, "scitex-storage", "runtime", "fleet-bubbles.html")


def default_sunburst_path() -> str:
    """Where the rendered capacity-sunburst page is cached (sibling of the others)."""
    base = os.environ.get("SCITEX_DIR") or os.path.expanduser("~/.scitex")
    return os.path.join(base, "scitex-storage", "runtime", "fleet-sunburst.html")


#: Shown when no snapshot exists yet. A named next step, not a blank page.
_NO_SNAPSHOT_HTML = (
    "<!doctype html><html><body style='font-family:sans-serif;"
    "background:#0d1117;color:#c9d1d9;padding:2rem'>"
    "<h1>SciTeX Storage &mdash; fleet</h1>"
    "<p>No fleet snapshot has been gathered yet.</p>"
    "<p>Run <code>scitex-storage fleet-status --gather</code> "
    "(or wait for the periodic job) to populate it.</p>"
    "</body></html>"
)


def fleet_html_or_placeholder(path: str) -> str:
    """Return the cached dashboard at ``path``, or a placeholder if absent.

    Pure with respect to its argument -- takes the path explicitly rather
    than resolving it -- so the read/placeholder branch is testable
    without env manipulation or a running server. "Absent" is a real
    first-run state (the gather has not run yet), so it yields a page
    naming the fix, never a blank body and never an exception.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return _NO_SNAPSHOT_HTML


def write_fleet_snapshot(
    path: str,
    generated_at: str,
    timeout_seconds: float = 30.0,
) -> FleetSnapshot:
    """Gather the whole fleet and write the rendered dashboard atomically.

    ``generated_at`` is passed in rather than read from the clock so the
    caller owns the timestamp (and the write is reproducible in a test).
    Rendering is imported lazily so this module does not pull the HTML
    layer into every import.

    The write is atomic (temp + rename) because a reader -- the Django
    view -- may hit this file at any instant, and a half-written
    dashboard is worse than a stale one. Returns the snapshot so a caller
    can inspect what was written without re-reading the file.
    """
    from ._bubble_render import build_bubbles_html
    from ._fleet_status_render import build_dashboard_html
    from ._sunburst_render import build_sunburst_html

    rows = observe_fleet(timeout_seconds=timeout_seconds)
    flagged = sum(1 for r in rows if r.is_flagged)
    could_not = sum(1 for r in rows if r.could_not_look)
    snapshot = FleetSnapshot(
        rows=rows,
        generated_at=generated_at,
        note=(
            f"{len({r.host for r in rows})} hosts, {len(rows)} filesystems, "
            f"{flagged} flagged, {could_not} could-not-look"
        ),
    )

    # Both views are pure renders of the same snapshot; write both so the
    # table and the bubble page never disagree about what was gathered.
    _atomic_write(path, build_dashboard_html(snapshot))
    _atomic_write(default_bubbles_path(), build_bubbles_html(snapshot))
    _atomic_write(default_sunburst_path(), build_sunburst_html(snapshot))
    return snapshot


def _atomic_write(path: str, text: str) -> None:
    """Write ``text`` to ``path`` via temp+rename -- a reader never sees half."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)  # atomic on POSIX

# EOF

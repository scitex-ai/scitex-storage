#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_observe.py
"""Multi-host observation: read every host in the registry, including the broken ones.

The host list comes from scitex-dev's registry, which is the SSOT --
scitex-storage does not keep a second list of machines. A parallel list
drifts, and a storage dashboard that silently omits a host is worse than
no dashboard: the host you forgot is exactly the one that fills up.

**An unreachable host is a ROW, never an absence.** If ssh fails, the
host still appears, rendered ``could-not-look``, with the reason. This
is the same discipline as the inode probe and it exists because failures
that break the reporting path erase their own evidence -- the incident
corpus is survivor-biased, so whatever surfaces health must not vanish
along with the thing it was watching.

Portability matters more than elegance here. The fleet spans GNU/Linux
(ywata-note-win, spartan), macOS (mba) and BusyBox appliances
(nas/nas1/nas2), so the remote probe uses only POSIX ``df -P`` / ``df
-Pi``. A BusyBox box has no ``sort -h``, no ``--output=``, no GNU long
options -- a fancier command silently produces an empty result that
reads as "host is fine", which is the failure mode that produces the
convenient answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import os

from ._fleet_status import COULD_NOT_LOOK, MEASURED, NOT_APPLICABLE, FleetSnapshot, HostStorage

#: Device-name prefixes/names whose "100% full" is structural, not a
#: warning. A read-only squashfs image (every `/snap/*`, every
#: appliance `/rootfs/*`) is packed exactly full BY CONSTRUCTION -- it
#: can never be anything else. A first live run across this fleet
#: produced dozens of 100% rows from these alone, which is how a
#: dashboard trains its reader to ignore red.
#:
#: Note this is decided from the SOURCE DEVICE, not the mount path.
#: Paths are a naming convention and vary per OS; `/dev/loop*` is what
#: the kernel actually reports.
#:
#: The list is deliberately per-form rather than per-concept, because
#: the SAME concept reports differently per host: the appliance NASes
#: mount their squashfs images as ``/dev/loop*`` while Ubuntu/WSL mounts
#: the identical thing as ``snapfuse``. A first version of this rule was
#: written from the NAS output alone, caught 233 rows, and still left 25
#: WSL snaps flagged -- a filter built from the contaminants you have
#: already seen only excludes the contaminants you have already seen.
PSEUDO_SOURCES = (
    "tmpfs",
    "devtmpfs",
    "udev",
    "devfs",
    "map",
    "overlay",
    "none",
    "snapfuse",
    "squashfs",
)
IMAGE_SOURCE_PREFIXES = ("/dev/loop",)


def is_structural(source: str) -> bool:
    """True when this filesystem's fullness carries no information.

    Squashfs images are always 100% full; tmpfs/devfs are memory, not
    storage. Reporting either as a capacity alarm is a false positive,
    and a dashboard full of false positives is worse than none -- it
    teaches the reader that red means nothing.
    """
    if source in PSEUDO_SOURCES:
        return True
    return source.startswith(IMAGE_SOURCE_PREFIXES)

#: What we ask a remote host. POSIX-portable on GNU, BSD/macOS and BusyBox.
DF_SPACE_CMD = "df -P"
DF_INODE_CMD = "df -Pi"


@dataclass(frozen=True)
class ProbeOutcome:
    """The raw result of running a command on a host.

    ``ok=False`` carries WHY, because "no output" and "ssh refused" and
    "command not found" need different fixes and are indistinguishable
    once collapsed to an empty string.
    """

    ok: bool
    stdout: str = ""
    error: str = ""


#: A callable that runs ``command`` on ``host`` and returns the outcome.
#: Injected so the parsing and assembly logic is testable without a
#: network, and so a caller can substitute a different transport
#: (a container exec, a listen daemon, a queue) without touching this
#: module.
Runner = Callable[[str, str], ProbeOutcome]


def parse_df_posix(text: str) -> list[dict[str, object]]:
    """Parse ``df -P``/``df -Pi`` output into rows.

    POSIX ``df -P`` guarantees one line per filesystem after the header,
    with the mount point LAST. It does NOT guarantee a column count:
    GNU ``df -Pi`` emits six fields, macOS emits nine
    (``... Capacity iused ifree %iused Mounted on``). Assuming six and
    slicing ``parts[5:]`` therefore swallows macOS's inode columns into
    the mount name, no mount ever matches, and every Mac filesystem
    reports "inodes unavailable" -- which is a wrong answer that looks
    like a limitation. Found by running this against the real fleet;
    the unit tests, written from GNU output, were perfectly happy.

    So the mount column index is read from the HEADER (``Mounted on``)
    rather than assumed, and the first three numeric columns after the
    filesystem name are taken as total/used/available -- which holds for
    both the block and inode variants on every df in this fleet.

    Rows that still do not parse are DROPPED rather than guessed at, and
    the caller sees a shorter list rather than a plausible fiction.
    """
    lines = text.splitlines()
    if not lines:
        return []

    header = lines[0].split()
    try:
        # "Mounted on" is two words; the mount VALUE starts at that index.
        mount_idx = header.index("Mounted")
    except ValueError:
        mount_idx = 5  # no recognisable header: fall back to the POSIX shape

    rows: list[dict[str, object]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= mount_idx:
            continue
        try:
            total = int(parts[1])
            used = int(parts[2])
            avail = int(parts[3])
        except (ValueError, IndexError):
            # A "-" or "none" column: common for tmpfs/devfs and for
            # inode columns on filesystems without a fixed inode table.
            continue
        rows.append(
            {
                "source": parts[0],
                "total": total,
                "used": used,
                "avail": avail,
                "mount": " ".join(parts[mount_idx:]),
            }
        )
    return rows


def used_pct(total: int, used: int) -> float | None:
    """Percentage used, or ``None`` when the total is zero/absent.

    Returns ``None`` rather than ``0.0`` for an empty filesystem table.
    Zero is a measurement meaning "nothing used"; ``None`` means "there
    was nothing to measure", and rendering the second as the first is how
    a dead mount becomes a reassuring green bar.
    """
    if total <= 0:
        return None
    return round(100.0 * used / total, 1)


def _index_by_mount(rows: Sequence[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(r["mount"]): r for r in rows}


def observe_host(
    host: str,
    role: str,
    runner: Runner,
    keep_mounts: Sequence[str] | None = None,
) -> list[HostStorage]:
    """Observe one host, returning at least one row no matter what.

    ``keep_mounts``, when given, restricts the result to those mount
    points -- a NAS reports dozens of tmpfs and lock mounts that are
    noise on a storage dashboard. When omitted, everything ``df``
    reported is returned; filtering is the caller's policy, not ours.
    """
    space = runner(host, DF_SPACE_CMD)
    if not space.ok:
        return [
            HostStorage(
                host=host,
                role=role,
                mount="(host)",
                verdict=COULD_NOT_LOOK,
                note=space.error or "probe failed with no error text",
            )
        ]

    space_rows = parse_df_posix(space.stdout)
    if not space_rows:
        # The command ran and said nothing intelligible. That is NOT an
        # empty machine -- far more likely a df we could not parse.
        return [
            HostStorage(
                host=host,
                role=role,
                mount="(host)",
                verdict=COULD_NOT_LOOK,
                note="df returned no parseable rows -- not the same as no filesystems",
            )
        ]

    inode = runner(host, DF_INODE_CMD)
    inode_by_mount = (
        _index_by_mount(parse_df_posix(inode.stdout)) if inode.ok else {}
    )

    results: list[HostStorage] = []
    for row in space_rows:
        mount = str(row["mount"])
        if keep_mounts is not None and mount not in keep_mounts:
            continue
        total = int(row["total"])
        source = str(row["source"])

        if is_structural(source):
            # Present, deliberately NOT flagged. Omitting it entirely
            # would be a different lie -- the filesystem is really there,
            # it simply cannot be "too full".
            results.append(
                HostStorage(
                    host=host,
                    role=role,
                    mount=mount,
                    verdict=NOT_APPLICABLE,
                    size_bytes=total * 1024,
                    used_pct=None,
                    inode_used_pct=None,
                    note=f"{source}: read-only image or pseudo-fs, always full by design",
                )
            )
            continue

        irow = inode_by_mount.get(mount)
        if irow is None:
            # Space measured, inodes not. Report the space we have and be
            # explicit that the inode figure is missing rather than zero.
            results.append(
                HostStorage(
                    host=host,
                    role=role,
                    mount=mount,
                    verdict=COULD_NOT_LOOK,
                    size_bytes=total * 1024,
                    used_pct=used_pct(total, int(row["used"])),
                    inode_used_pct=None,
                    note="inode figures unavailable on this host",
                )
            )
            continue
        results.append(
            HostStorage(
                host=host,
                role=role,
                mount=mount,
                verdict=MEASURED,
                size_bytes=total * 1024,
                used_pct=used_pct(total, int(row["used"])),
                inode_used_pct=used_pct(int(irow["total"]), int(irow["used"])),
            )
        )

    if not results:
        return [
            HostStorage(
                host=host,
                role=role,
                mount="(host)",
                verdict=COULD_NOT_LOOK,
                note="no mounts matched the requested filter",
            )
        ]
    return results


# --------------------------------------------------------------------------
# Transport + fleet assembly
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


def observe_fleet(
    keep_mounts_by_host: dict[str, Sequence[str]] | None = None,
    timeout_seconds: float = 30.0,
) -> list[HostStorage]:
    """Observe every host in the scitex-dev registry (the SSOT).

    scitex-storage keeps NO host list of its own. If a machine is missing
    here it is missing from the registry, and that is one place to fix
    rather than two places to drift.

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
    from ._fleet_status_render import build_dashboard_html

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
    html = build_dashboard_html(snapshot)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, path)  # atomic on POSIX
    return snapshot

# EOF

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_observe/_hosts.py
"""Per-host observation and HPC project discovery.

Split out of ``_observe`` (which re-exports these). The transport is
INJECTED (a ``Runner``) so every function here is testable without a
network. An unreachable or unparseable host is always a ROW -- never an
absence and never a reassuring green zero -- because a failure that
erases its own evidence is the most dangerous kind on a health surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .._fleet_status import COULD_NOT_LOOK, MEASURED, NOT_APPLICABLE, HostStorage
from ._df import (
    DF_INODE_CMD,
    DF_SPACE_CMD,
    index_by_mount,
    is_structural,
    parse_df_posix,
    used_pct,
)

#: On an HPC login node, the global mount table is ~200 rows of cluster
#: infrastructure (/cvmfs, /apps, node-local scratch, ...), and NONE of
#: it is the user's storage. Their allocation is a GPFS/Lustre FILESET
#: under a project root, which only reports its own quota when you df the
#: SPECIFIC PATH -- in the global df it is folded into one parent mount.
#: So for HPC the relevant view is discovered from the user's groups, not
#: filtered from df. This is Spartan's layout; the project root is
#: configurable because another cluster will differ.
HPC_PROJECT_ROOT = "/data/gpfs/projects"
#: Group-name prefixes that denote a project allocation rather than a
#: system group (staff, users, ...). Spartan projects are punimNNNN.
HPC_PROJECT_GROUP_PREFIXES = ("punim", "pawsey", "hpc")


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
        index_by_mount(parse_df_posix(inode.stdout)) if inode.ok else {}
    )

    results: list[HostStorage] = []
    for row in space_rows:
        mount = str(row["mount"])
        if keep_mounts is not None and mount not in keep_mounts:
            continue
        blk = int(row.get("block_bytes", 1024))
        total_bytes = int(row["total"]) * blk
        avail_bytes = int(row["avail"]) * blk
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
                    size_bytes=total_bytes,
                    used_pct=None,
                    inode_used_pct=None,
                    avail_bytes=avail_bytes,
                    source=source,
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
                    size_bytes=total_bytes,
                    used_pct=used_pct(int(row["total"]), int(row["used"])),
                    inode_used_pct=None,
                    avail_bytes=avail_bytes,
                    source=source,
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
                size_bytes=total_bytes,
                used_pct=used_pct(int(row["total"]), int(row["used"])),
                inode_used_pct=used_pct(int(irow["total"]), int(irow["used"])),
                avail_bytes=avail_bytes,
                source=source,
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


def parse_project_groups(
    groups_output: str,
    prefixes: Sequence[str] = HPC_PROJECT_GROUP_PREFIXES,
) -> list[str]:
    """Extract project-allocation group names from ``groups`` output.

    ``groups`` prints space-separated group names. We keep only those
    that look like a project (by prefix), dropping system groups. Pure so
    the prefix policy is testable without a login node.
    """
    names = groups_output.split()
    return [g for g in names if g.startswith(tuple(prefixes))]


def observe_hpc_projects(
    host: str,
    role: str,
    runner: Runner,
    project_root: str = HPC_PROJECT_ROOT,
) -> list[HostStorage]:
    """Observe an HPC host's PROJECT ALLOCATIONS, discovered from groups.

    Runs ``groups`` to learn which projects the user belongs to, then
    ``df -P``/``df -Pi`` on each ``<project_root>/<group>`` path so the
    fileset reports its own quota. Returns one row per allocation.

    Falls back to a single could-not-look row if ``groups`` cannot be
    read -- an HPC host whose allocations we cannot enumerate is
    unobserved, not empty, and must not read as "nothing to watch".
    """
    g = runner(host, "groups")
    if not g.ok:
        return [
            HostStorage(
                host=host,
                role=role,
                mount="(projects)",
                verdict=COULD_NOT_LOOK,
                note=f"could not read groups: {g.error}",
            )
        ]

    projects = parse_project_groups(g.stdout)
    if not projects:
        return [
            HostStorage(
                host=host,
                role=role,
                mount="(projects)",
                verdict=COULD_NOT_LOOK,
                note="no project groups found in `groups` output",
            )
        ]

    rows: list[HostStorage] = []
    for proj in projects:
        path = f"{project_root}/{proj}"
        space = runner(host, f"df -P {path}")
        inode = runner(host, f"df -Pi {path}")
        srows = parse_df_posix(space.stdout) if space.ok else []
        irows = parse_df_posix(inode.stdout) if inode.ok else []
        if not srows:
            rows.append(
                HostStorage(
                    host=host,
                    role=role,
                    mount=path,
                    verdict=COULD_NOT_LOOK,
                    note=space.error or "df on the project path returned nothing",
                )
            )
            continue
        s = srows[0]
        blk = int(s.get("block_bytes", 1024))
        iu = (
            used_pct(int(irows[0]["total"]), int(irows[0]["used"]))
            if irows
            else None
        )
        rows.append(
            HostStorage(
                host=host,
                role=role,
                mount=path,  # the PROJECT path, not the shared parent mount
                verdict=MEASURED if irows else COULD_NOT_LOOK,
                size_bytes=int(s["total"]) * blk,
                used_pct=used_pct(int(s["total"]), int(s["used"])),
                inode_used_pct=iu,
                avail_bytes=int(s["avail"]) * blk,
                source=str(s["source"]),
                note="" if irows else "inode figures unavailable for this fileset",
            )
        )
    return rows

# EOF

"""Unit tests for scitex_storage._observe (multi-host observation).

The runner is injected, so every case here uses a REAL callable returning
real recorded output -- no mocks, and no network. The df samples are the
actual shapes this fleet emits: GNU coreutils, BusyBox (nas/nas1/nas2)
and macOS (mba).

The rule under test throughout: an unreachable or unparseable host is a
ROW that says could-not-look, never an absence and never a green zero.
"""

from __future__ import annotations

from scitex_storage._fleet_status import COULD_NOT_LOOK, MEASURED, NOT_APPLICABLE
from scitex_storage._observe import (
    ProbeOutcome,
    is_structural,
    observe_host,
    observe_hpc_projects,
    parse_df_posix,
    parse_project_groups,
    subprocess_runner,
    used_pct,
)

GNU_DF = """Filesystem     1024-blocks       Used Available Capacity Mounted on
/dev/sdd        2113646364 1382874624 623421944      69% /
tmpfs              8168604          0   8168604       0% /dev/shm
"""

BUSYBOX_DF = """Filesystem           1024-blocks      Used Available Capacity Mounted on
/dev/mapper/vol1      28843569216 8493823616 19298745600  31% /share/CACHEDEV1_DATA
tmpfs                       16384         0       16384   0% /share
"""

GNU_DF_INODES = """Filesystem      Inodes  IUsed     IFree IUse% Mounted on
/dev/sdd     131072000 27314178 103757822   21% /
tmpfs          2042151        1   2042150    1% /dev/shm
"""

MOUNT_WITH_SPACE = """Filesystem 1024-blocks Used Available Capacity Mounted on
/dev/disk2 100 40 60 40% /Volumes/My Backup Disk
"""


def _runner_returning(mapping):
    """A real callable: command -> outcome. Not a mock, just a lookup."""

    def run(host, command):
        return mapping.get(command, ProbeOutcome(ok=False, error="unexpected command"))

    return run


# --- parse_df_posix --------------------------------------------------------
def test_gnu_df_yields_one_row_per_real_filesystem():
    # Assert
    assert len(parse_df_posix(GNU_DF)) == 2


def test_busybox_df_parses_identically():
    # The NAS units run BusyBox; a GNU-only parser would silently return
    # nothing and read as "this NAS has no filesystems".
    # Assert
    assert parse_df_posix(BUSYBOX_DF)[0]["mount"] == "/share/CACHEDEV1_DATA"


def test_macos_512_byte_blocks_are_not_doubled():
    # macOS `df -P` reports 512-byte blocks; assuming 1024 doubled every
    # Mac size (mba read 2.7T for a 245G disk). block_bytes must be 512.
    # Arrange
    text = (
        "Filesystem     512-blocks      Used Available Capacity  Mounted on\n"
        "/dev/disk3s5    478724992 272132024 123368104     69%    /Data\n"
    )
    # Act
    row = parse_df_posix(text)[0]
    # Assert
    assert row["block_bytes"] == 512


def test_gnu_1024_byte_blocks_are_detected():
    # Assert
    assert parse_df_posix(GNU_DF)[0]["block_bytes"] == 1024


def test_a_mount_point_containing_spaces_survives():
    # POSIX -P puts the mount LAST precisely because it may contain spaces.
    # Assert
    assert parse_df_posix(MOUNT_WITH_SPACE)[0]["mount"] == "/Volumes/My Backup Disk"


def test_macos_inode_output_with_nine_columns_keeps_the_right_mount():
    # macOS `df -Pi` emits ...Capacity iused ifree %iused Mounted on.
    # Assuming GNU's six columns swallowed the inode fields into the
    # mount name, so no mount ever matched and every Mac filesystem
    # reported "inodes unavailable". Caught against the real fleet.
    # Arrange
    text = (
        "Filesystem   512-blocks      Used Available Capacity iused      ifree %iused  Mounted on\n"
        "/dev/disk3s1  965595304 123456789 800000000     14%  501234 3900000000    0%   /System/Volumes/Data\n"
    )

    # Assert
    assert parse_df_posix(text)[0]["mount"] == "/System/Volumes/Data"


def test_unparseable_rows_are_dropped_rather_than_guessed():
    # Arrange
    text = "Filesystem 1024-blocks Used Available Capacity Mounted on\nmap -hosts - - - - /net\n"

    # Assert
    assert parse_df_posix(text) == []


# --- used_pct --------------------------------------------------------------
def test_an_empty_filesystem_table_is_none_not_zero():
    # Zero means "nothing used"; None means "nothing to measure". Rendering
    # the second as the first turns a dead mount into a green bar.
    # Assert
    assert used_pct(total=0, used=0) is None


def test_used_percentage_is_rounded_to_one_decimal():
    # Assert
    assert used_pct(total=1000, used=694) == 69.4


# --- observe_host ----------------------------------------------------------
def test_an_unreachable_host_still_produces_a_row():
    # Arrange
    runner = _runner_returning({})

    # Act
    rows = observe_host("nas9", "storage", runner)

    # Assert
    assert len(rows) == 1


def test_an_unreachable_host_is_marked_could_not_look():
    # Arrange
    runner = _runner_returning({})

    # Act
    rows = observe_host("nas9", "storage", runner)

    # Assert
    assert rows[0].verdict == COULD_NOT_LOOK


def test_the_failure_reason_is_carried_not_discarded():
    # Arrange
    def run(host, command):
        return ProbeOutcome(ok=False, error="ssh: connect to host nas9 port 22: timed out")

    # Act
    rows = observe_host("nas9", "storage", run)

    # Assert
    assert "timed out" in rows[0].note


def test_output_that_parses_to_nothing_is_not_reported_as_an_empty_machine():
    # Arrange
    runner = _runner_returning({"df -P": ProbeOutcome(ok=True, stdout="garbage\n")})

    # Act
    rows = observe_host("nas9", "storage", runner)

    # Assert
    assert rows[0].verdict == COULD_NOT_LOOK


def test_space_and_inodes_together_are_measured():
    # Arrange
    runner = _runner_returning(
        {
            "df -P": ProbeOutcome(ok=True, stdout=GNU_DF),
            "df -Pi": ProbeOutcome(ok=True, stdout=GNU_DF_INODES),
        }
    )

    # Act
    rows = observe_host("ywata-note-win", "workstation", runner, keep_mounts=["/"])

    # Assert
    assert rows[0].verdict == MEASURED


def test_the_inode_percentage_is_computed_from_the_inode_table():
    # Arrange
    runner = _runner_returning(
        {
            "df -P": ProbeOutcome(ok=True, stdout=GNU_DF),
            "df -Pi": ProbeOutcome(ok=True, stdout=GNU_DF_INODES),
        }
    )

    # Act
    rows = observe_host("ywata-note-win", "workstation", runner, keep_mounts=["/"])

    # Assert
    assert rows[0].inode_used_pct == 20.8


def test_space_without_inodes_still_reports_the_space():
    # A host where `df -Pi` is unsupported must not lose its space figure.
    # Arrange
    runner = _runner_returning({"df -P": ProbeOutcome(ok=True, stdout=GNU_DF)})

    # Act
    rows = observe_host("mba", "workstation", runner, keep_mounts=["/"])

    # Assert
    assert rows[0].used_pct == 65.4


def test_space_without_inodes_leaves_the_inode_figure_absent_not_zero():
    # Arrange
    runner = _runner_returning({"df -P": ProbeOutcome(ok=True, stdout=GNU_DF)})

    # Act
    rows = observe_host("mba", "workstation", runner, keep_mounts=["/"])

    # Assert
    assert rows[0].inode_used_pct is None


def test_a_nonzero_exit_that_still_printed_output_counts_as_success():
    # `df` exits non-zero when ANY one mount is unreadable. One stale
    # samba share on nas1/nas2 made the entire host report
    # could-not-look while df had already printed every other
    # filesystem. A real subprocess, not a mock.
    # Arrange
    run = subprocess_runner(None, timeout_seconds=10)

    # Act
    outcome = run("local", "sh -c 'echo real-output; exit 1'")

    # Assert
    assert outcome.ok is True


def test_a_partial_failure_still_reports_why_it_was_partial():
    # Arrange
    run = subprocess_runner(None, timeout_seconds=10)

    # Act
    outcome = run("local", "sh -c 'echo out; echo bad-mount >&2; exit 1'")

    # Assert
    assert "partial" in outcome.error


def test_a_nonzero_exit_with_no_output_is_still_a_failure():
    # Arrange
    run = subprocess_runner(None, timeout_seconds=10)

    # Act
    outcome = run("local", "sh -c 'exit 3'")

    # Assert
    assert outcome.ok is False


SPARTAN_GROUPS = "punim2354 punim0264 unix staff\n"
GPFS_PROJECT_DF = """Filesystem 1024-blocks Used Available Capacity Mounted on
project 9663676416 6871947673 2791728743 72% /data/gpfs
"""
GPFS_PROJECT_DF_I = """Filesystem Inodes IUsed IFree IUse% Mounted on
project 7000000 6808212 191788 98% /data/gpfs
"""


# --- HPC project discovery -------------------------------------------------
def test_project_groups_keep_only_allocation_groups():
    # Arrange -- real Spartan groups: two projects plus system groups.
    # Act
    projects = parse_project_groups(SPARTAN_GROUPS)

    # Assert
    assert projects == ["punim2354", "punim0264"]


def test_hpc_observes_one_row_per_project_allocation():
    # The whole point: a login node's 200 infra mounts are noise; the
    # user's TWO project allocations are the signal.
    # Arrange
    def run(host, command):
        if command == "groups":
            return ProbeOutcome(ok=True, stdout=SPARTAN_GROUPS)
        if command.startswith("df -Pi"):
            return ProbeOutcome(ok=True, stdout=GPFS_PROJECT_DF_I)
        return ProbeOutcome(ok=True, stdout=GPFS_PROJECT_DF)

    # Act
    rows = observe_hpc_projects("spartan", "hpc-login", run)

    # Assert
    assert len(rows) == 2


def test_hpc_row_mount_is_the_project_path_not_the_shared_parent():
    # On GPFS the parent mount /data/gpfs is shared; the PROJECT path is
    # what carries the per-allocation quota.
    # Arrange
    def run(host, command):
        if command == "groups":
            return ProbeOutcome(ok=True, stdout="punim0264\n")
        if command.startswith("df -Pi"):
            return ProbeOutcome(ok=True, stdout=GPFS_PROJECT_DF_I)
        return ProbeOutcome(ok=True, stdout=GPFS_PROJECT_DF)

    # Act
    rows = observe_hpc_projects("spartan", "hpc-login", run)

    # Assert
    assert rows[0].mount == "/data/gpfs/projects/punim0264"


def test_hpc_reads_the_fileset_inode_quota():
    # punim0264 was 98% inodes -- exactly the alarm the global df hides.
    # Arrange
    def run(host, command):
        if command == "groups":
            return ProbeOutcome(ok=True, stdout="punim0264\n")
        if command.startswith("df -Pi"):
            return ProbeOutcome(ok=True, stdout=GPFS_PROJECT_DF_I)
        return ProbeOutcome(ok=True, stdout=GPFS_PROJECT_DF)

    # Act
    rows = observe_hpc_projects("spartan", "hpc-login", run)

    # Assert
    assert rows[0].inode_used_pct == 97.3


def test_hpc_with_unreadable_groups_is_could_not_look_not_empty():
    # Arrange
    def run(host, command):
        return ProbeOutcome(ok=False, error="ssh timed out")

    # Act
    rows = observe_hpc_projects("spartan", "hpc-login", run)

    # Assert
    assert rows[0].verdict == COULD_NOT_LOOK


def test_a_squashfs_image_is_structural_not_an_alarm():
    # Every /snap/* mount is a read-only squashfs packed exactly full.
    # A live run produced dozens of 100% rows from these alone.
    # Assert
    assert is_structural("/dev/loop12") is True


def test_tmpfs_is_structural():
    # Assert
    assert is_structural("tmpfs") is True


def test_snapfuse_is_structural_too():
    # The SAME concept reports differently per host: the NAS appliances
    # show squashfs images as /dev/loop*, Ubuntu/WSL shows the identical
    # thing as `snapfuse`. Covering only the first left 25 WSL snaps
    # flagged in a live run.
    # Assert
    assert is_structural("snapfuse") is True


def test_a_real_block_device_is_not_structural():
    # Assert
    assert is_structural("/dev/sdd") is False


def test_a_structural_filesystem_is_reported_but_not_measured():
    # It is really mounted, so omitting it would be its own lie.
    # Arrange
    text = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/loop9 128000 128000 0 100% /snap/core22/2339\n"
    )
    runner = _runner_returning({"df -P": ProbeOutcome(ok=True, stdout=text)})

    # Act
    rows = observe_host("ywata-note-win", "workstation", runner)

    # Assert
    assert rows[0].verdict == NOT_APPLICABLE


def test_a_structural_filesystem_carries_no_percentage_to_flag():
    # Arrange
    text = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/loop9 128000 128000 0 100% /snap/core22/2339\n"
    )
    runner = _runner_returning({"df -P": ProbeOutcome(ok=True, stdout=text)})

    # Act
    rows = observe_host("ywata-note-win", "workstation", runner)

    # Assert
    assert rows[0].used_pct is None


def test_a_filter_matching_nothing_is_reported_rather_than_returning_empty():
    # An empty list would read as "this host has no storage".
    # Arrange
    runner = _runner_returning({"df -P": ProbeOutcome(ok=True, stdout=GNU_DF)})

    # Act
    rows = observe_host("nas", "storage", runner, keep_mounts=["/nonexistent"])

    # Assert
    assert rows[0].verdict == COULD_NOT_LOOK

# EOF

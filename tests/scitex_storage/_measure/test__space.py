"""Unit tests for scitex_storage._measure._space (remote free-space probe).

The defect these guard is the one card
sweep-writes-tar-to-source-filesystem-20260722 was written about: a verb
that relieves a constrained resource, not measuring the resource it
depends on. `sweep` got its preflight in PR #29; `archive` and `reclaim`
did not, and they are the verbs that actually move data OFF a full
filesystem.

Parsing is pure, so none of this needs `monkeypatch` (banned here).
"""

from __future__ import annotations

from scitex_storage._measure._space import parse_df_available_bytes


def test_a_normal_df_row_yields_available_bytes():
    # Arrange -- `df -Pk` on a roomy destination.
    stdout = (
        "Filesystem     1024-blocks       Used  Available Capacity Mounted on\n"
        "/dev/sda1       28836528128 8000000000 20836528128      28% /share\n"
    )

    # Act
    available = parse_df_available_bytes(stdout)

    # Assert
    assert available == 20836528128 * 1024


def test_a_full_destination_reports_zero_not_none():
    # Zero is a MEASUREMENT ("full"), and must refuse loudly rather than
    # being confused with "could not answer".
    # Arrange
    stdout = (
        "Filesystem     1024-blocks       Used  Available Capacity Mounted on\n"
        "/dev/sda1        1000000000 1000000000          0     100% /share\n"
    )

    # Act
    available = parse_df_available_bytes(stdout)

    # Assert
    assert available == 0


def test_a_warning_line_before_the_row_does_not_break_parsing():
    # BusyBox df on the NAS units emits warnings for stale mounts; -P
    # guarantees the real row is a single line, so the LAST line is it.
    # Arrange
    stdout = (
        "df: /mnt/stale: Stale file handle\n"
        "Filesystem     1024-blocks       Used  Available Capacity Mounted on\n"
        "/dev/sda1        1000000000  400000000  600000000      40% /share\n"
    )

    # Act
    available = parse_df_available_bytes(stdout)

    # Assert
    assert available == 600000000 * 1024


def test_empty_output_is_none_not_zero():
    # A probe that produced nothing must not read as "the disk is full",
    # and must not read as "there is room" either.
    # Arrange
    stdout = ""

    # Act
    available = parse_df_available_bytes(stdout)

    # Assert
    assert available is None


def test_a_header_with_no_row_is_none():
    # Arrange
    stdout = "Filesystem 1024-blocks Used Available Capacity Mounted on\n"

    # Act
    available = parse_df_available_bytes(stdout)

    # Assert
    assert available is None


def test_a_non_numeric_available_column_is_none():
    # Arrange
    stdout = (
        "Filesystem     1024-blocks       Used  Available Capacity Mounted on\n"
        "/dev/sda1        1000000000  400000000          -      40% /share\n"
    )

    # Act
    available = parse_df_available_bytes(stdout)

    # Assert
    assert available is None


def test_a_truncated_row_is_none():
    # Arrange -- fewer than the four columns the Available field needs.
    stdout = "Filesystem 1024-blocks\n/dev/sda1 1000\n"

    # Act
    available = parse_df_available_bytes(stdout)

    # Assert
    assert available is None

# EOF

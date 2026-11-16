"""Real separate-device reclaim coverage; no crash/durability simulation."""

import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main
from scitex_storage._transfer._reclaim import (
    apply_reclaim,
    plan_reclaim,
    restore_reclaim,
)


@pytest.fixture
def filesystem_pair(tmp_path):
    second_root = Path("/dev/shm")
    if not second_root.is_dir() or second_root.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("requires an accessible second filesystem at /dev/shm")
    home = tmp_path / "home"
    home.mkdir()
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        with tempfile.TemporaryDirectory(
            prefix="scitex-reclaim-", dir=second_root
        ) as remote:
            yield tmp_path, Path(remote)
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


@pytest.fixture(params=[False, True], ids=["file", "directory"])
def reclaim_case(filesystem_pair, request):
    first, second = filesystem_pair
    source = first / "payload"
    if request.param:
        source.mkdir()
        content = source / "data.bin"
        relative = ("data.bin",)
    else:
        content = source
        relative = ()
    content.write_bytes(b"real cross-filesystem payload")
    return first, second, source, content, relative


def test_reclaim_round_trip_across_real_filesystems(reclaim_case):
    # Arrange -- only owned, small fixture files on two actual devices.
    first, second, source, content, relative = reclaim_case
    plan = plan_reclaim([source], run_id="cross-device", archive_root=str(second))
    destination = Path(plan.entries[0].archived)
    # Act
    manifest = apply_reclaim(plan)
    archived = destination.joinpath(*relative)
    moved = (
        source.exists(),
        archived.read_bytes(),
        archived.stat().st_dev != first.stat().st_dev,
    )
    restored = restore_reclaim(manifest.run_id)
    # Assert -- completed copy/restore only, not interruption or crash safety.
    assert (moved, content.read_bytes(), destination.exists(), restored.restored) == (
        (False, b"real cross-filesystem payload", True),
        b"real cross-filesystem payload",
        False,
        True,
    )


def test_reclaim_help_describes_unverified_cross_filesystem_copy():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim", "--help"])
    text = " ".join(result.output.split())
    # Assert
    assert (
        result.exit_code,
        "not independently checksum-verified" in text,
        "resumable" in text,
    ) == (0, True, True)

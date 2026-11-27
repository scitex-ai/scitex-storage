"""Real local rsync and shell read-back exercise tier, restore and cleanup.

Same transport pattern as ``test_archive_integrity.py``: commands, rsync,
tally, sha256sum and exit statuses run for real; only the ssh host namespace
is translated, and only inside the test's own temp tree. This transport also
translates PULLS, so a tiered unit can be restored from its original path.
"""

import json
import os
import shutil
import subprocess
import time
from collections import namedtuple
from pathlib import Path

import pytest

from scitex_storage._transfer._archive import ArchiveManifest
from scitex_storage._transfer._archive_transport import DESTINATIONS
from scitex_storage._transfer._remote_target import remote_target_verifier
from scitex_storage._transfer._retention import sweep_restorable
from scitex_storage._transfer._scrub import scrub_manifest
from scitex_storage._transfer._tier import (
    apply_tier,
    plan_tier,
    pointer_path_for,
    read_pointer,
    restore_tiered,
)

pytestmark = pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("sha256sum") is None,
    reason="needs real rsync and sha256sum",
)

DAY = 86_400
Usage = namedtuple("Usage", "total used free")
HOST = "scitex-nas-02"


class LocalTransport:
    """Run real transport commands locally, restricted to the owned temp tree."""

    def __init__(self, root, *, mutation=None):
        self.root = Path(root)
        self.mutation = mutation

    def _local(self, arg):
        for alias in DESTINATIONS:
            if arg.startswith(alias + ":"):
                path = arg.split(":", 1)[1]
                if not Path(path).is_relative_to(self.root):
                    raise ValueError("Transport escaped owned fixture")
                return path
        return arg

    def __call__(self, command, **kwargs):
        kwargs.pop("timeout", None)
        if command[0] == "ssh":
            script = command[-1]
            if str(self.root) not in script:
                raise ValueError("Command escaped owned transport fixture")
            return subprocess.run(["sh", "-c", script], **kwargs)
        if command[0] == "scp":
            return subprocess.run(
                ["cp", command[-2], self._local(command[-1])], **kwargs
            )
        if Path(command[0]).name == "rsync":
            args = [self._local(a) for a in command]
            result = subprocess.run(args, **kwargs)
            if self.mutation:
                self.mutation()
            return result
        raise ValueError("Unsupported transport command")


@pytest.fixture
def world(tmp_path):
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path / "home")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "self").symlink_to(f"/proc/{os.getpid()}")
    volume = tmp_path / "volume"
    (volume / "backups").mkdir(parents=True)
    (volume / "old-project" / "raw").mkdir(parents=True)
    (volume / "backups" / "db.dump").write_bytes(b"dump bytes " * 50)
    (volume / "old-project" / "raw" / "a.bin").write_bytes(b"a" * 400)
    (volume / "old-project" / "notes.txt").write_bytes(b"n" * 300)
    stamp = time.time() - 400 * DAY
    for dirpath, _dirs, files in os.walk(volume, topdown=False):
        for name in files:
            os.utime(os.path.join(dirpath, name), (stamp, stamp))
        os.utime(dirpath, (stamp, stamp))
    # A small file written today keeps `backups` from moving whole, so the
    # cold dump in it moves as a single-file unit.
    (volume / "backups" / "today.log").write_bytes(b"t")
    os.utime(volume / "backups", (stamp, stamp))
    try:
        yield tmp_path, volume, proc
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def _plan(tmp_path, volume, proc):
    return plan_tier(
        volume,
        destination=HOST,
        remote_root=str(tmp_path / "remote"),
        cold_after_days=30,
        min_size_bytes=100,
        disk_usage=lambda _p: Usage(10_000, 9_000, 1_000),
        proc=proc,
    )


def _tier(world, **kw):
    tmp_path, volume, proc = world
    plan = _plan(tmp_path, volume, proc)
    return plan, apply_tier(plan, runner=LocalTransport(tmp_path, **kw), proc=proc)


def test_tier_moves_every_unit_and_leaves_pointers(world):
    # Arrange
    _tmp, volume, _proc = world
    # Act
    _plan_, log = _tier(world)
    # Assert
    assert (
        [e["status"] for e in log["units"]],
        pointer_path_for(volume / "backups" / "db.dump").exists(),
        pointer_path_for(volume / "old-project").exists(),
        (volume / "backups" / "db.dump").exists(),
    ) == (["tiered", "tiered"], True, True, False)


def test_remote_holds_the_tiered_file_byte_for_byte(world):
    # Arrange
    tmp_path, volume, _proc = world
    # Act
    _tier(world)
    # Assert
    pointer = read_pointer(volume / "backups" / "db.dump")
    assert (
        Path(pointer["remote_path"]) / "db.dump"
    ).read_bytes() == b"dump bytes " * 50


def test_pointer_records_a_content_verified_move(world):
    # Arrange
    _tmp, volume, _proc = world
    # Act
    _tier(world)
    # Assert
    pointer = read_pointer(volume / "backups" / "db.dump")
    assert (
        pointer["verification_method"],
        pointer["verified"],
        pointer["destination"],
        pointer["kind"],
        pointer["restore_hint"].startswith("restore with: scitex-storage restore"),
    ) == ("content", "verified", HOST, "file", True)


def test_run_log_on_disk_matches_the_returned_log(world):
    # Arrange
    # Act
    _plan_, log = _tier(world)
    # Assert
    assert json.loads(Path(log["log_path"]).read_text()) == log


def test_corrupted_destination_leaves_source_and_no_pointer(world):
    # Arrange
    tmp_path, volume, proc = world
    plan = _plan(tmp_path, volume, proc)
    target = Path(plan.units[0].remote_path)

    def corrupt():
        for path in target.rglob("*"):
            if path.is_file():
                path.write_bytes(b"corrupted")

    # Act
    log = apply_tier(plan, runner=LocalTransport(tmp_path, mutation=corrupt), proc=proc)
    # Assert
    first = Path(plan.units[0].path)
    assert (
        log["units"][0]["status"],
        first.exists(),
        pointer_path_for(first).exists(),
    ) == ("failed", True, False)


def test_restore_from_original_path_brings_file_back_and_drops_pointer(world):
    # Arrange
    tmp_path, volume, _proc = world
    _tier(world)
    original = volume / "backups" / "db.dump"
    # Act
    restore_tiered(original, runner=LocalTransport(tmp_path))
    # Assert
    assert (original.read_bytes(), pointer_path_for(original).exists()) == (
        b"dump bytes " * 50,
        False,
    )


def test_restore_of_tiered_directory_reproduces_its_tree(world):
    # Arrange
    tmp_path, volume, _proc = world
    _tier(world)
    original = volume / "old-project"
    # Act
    restore_tiered(original, runner=LocalTransport(tmp_path))
    # Assert
    assert sorted(
        str(p.relative_to(original)) for p in original.rglob("*") if p.is_file()
    ) == ["notes.txt", "raw/a.bin"]


def test_restore_refuses_to_overwrite_a_new_occupant(world):
    # Arrange
    tmp_path, volume, _proc = world
    _tier(world)
    original = volume / "backups" / "db.dump"
    original.write_bytes(b"new data")
    # Act
    refused = False
    try:
        restore_tiered(original, runner=LocalTransport(tmp_path))
    except FileExistsError:
        refused = True
    # Assert
    assert (refused, original.read_bytes()) == (True, b"new data")


def test_scrub_verifies_a_tiered_file_against_its_manifest(world):
    # Arrange
    tmp_path, volume, _proc = world
    _tier(world)
    pointer = read_pointer(volume / "backups" / "db.dump")
    manifest = ArchiveManifest.from_dict(
        json.loads(Path(pointer["manifest_path"]).read_text())
    )
    # Act
    report = scrub_manifest(manifest, content=True, runner=LocalTransport(tmp_path))
    # Assert
    assert report.content == "verified"


def test_remote_verifier_lets_expired_tiered_originals_be_swept(world):
    # Arrange
    tmp_path, _volume, _proc = world
    _plan_, log = _tier(world)
    staged = [e["staged_path"] for e in log["units"]]
    # Act
    report = sweep_restorable(
        dry_run=False,
        now=time.time() + 8 * DAY,
        target_verifier=remote_target_verifier(runner=LocalTransport(tmp_path)),
    )
    # Assert
    assert (sorted(report.removed), any(Path(p).exists() for p in staged)) == (
        sorted(staged),
        False,
    )


def test_remote_verifier_keeps_originals_whose_copy_changed(world):
    # Arrange
    tmp_path, volume, _proc = world
    _plan_, log = _tier(world)
    pointer = read_pointer(volume / "backups" / "db.dump")
    (Path(pointer["remote_path"]) / "db.dump").write_bytes(b"rotted")
    # Act
    sweep_restorable(
        dry_run=False,
        now=time.time() + 8 * DAY,
        target_verifier=remote_target_verifier(runner=LocalTransport(tmp_path)),
    )
    # Assert
    assert Path(pointer["staged_path"]).exists()

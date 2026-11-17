"""Single-use reclaim run IDs; intent retention is not automatic recovery."""

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from ._durable import file_lock, sync_directory
from ._reclaim_paths import _entry_path


def _validate_record_location(manifest_path: Path, entries) -> None:
    """A run must not move the tree containing its own records or lock inode."""
    # An upstream access path can lie in a selected tree even if a later
    # .scitex symlink redirects the actual records outside that tree.
    access_paths = [parent.resolve() for parent in manifest_path.parents]
    for entry in entries:
        source = _entry_path(entry.original)
        if stat.S_ISDIR(source.lstat().st_mode) and any(
            path.is_relative_to(source) for path in access_paths
        ):
            raise ValueError(f"Reclaim source contains its run records: {source}")


def _require_unused_run(manifest_path: Path) -> None:
    """Refuse existing records, including malformed files and dangling links."""
    for path in (manifest_path, manifest_path.with_suffix(".started")):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise FileExistsError(
            f"Reclaim run is already recorded or started: {path}; "
            "inspect its records and choose a new run_id"
        )


@contextmanager
def _claim_run(manifest_path: Path, plan):
    """Serialize cooperating applies and record intent before moving sources.

    Lock files remain in place to keep a stable lock inode. Started records
    remain even after a failed run: retrying that ID cannot hide a partial run.
    Neither record is a resume implementation or a guarantee about moved data
    surviving crashes. Advisory locking depends on filesystem lock support.
    """
    with file_lock(manifest_path.with_suffix(".lock"), blocking=False):
        _require_unused_run(manifest_path)
        intent = {
            "run_id": plan.run_id,
            "archive_root": plan.archive_root,
            "entries": [vars(entry) for entry in plan.entries],
        }
        started = manifest_path.with_suffix(".started")
        with started.open("x", encoding="utf-8") as stream:
            json.dump(intent, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(manifest_path.parent)
        yield

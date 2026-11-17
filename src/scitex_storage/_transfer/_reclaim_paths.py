"""Reject reclaim path dependencies before a run moves its first entry."""

import stat
from pathlib import Path


def _entry_path(raw: str) -> Path:
    """Resolve parent aliases while keeping a final symlink as its own entry."""
    path = Path(raw).expanduser().absolute()
    return path.parent.resolve() / path.name


def _validate_path_boundaries(entries) -> None:
    """Refuse nested sources/destinations and archives inside selected trees.

    This is a preflight snapshot, not protection against concurrent path changes
    or a transactional/crash-recovery mechanism. Final symlinks are moved as
    links; their targets do not become selected source directories.
    """
    sources = [_entry_path(entry.original) for entry in entries]
    destinations = [_entry_path(entry.archived) for entry in entries]
    directories = [stat.S_ISDIR(path.lstat().st_mode) for path in sources]
    for index, source in enumerate(sources):
        for other in sources[index + 1 :]:
            if source == other or (directories[index] and other.is_relative_to(source)):
                raise ValueError(f"Reclaim sources overlap: {source} and {other}")
        if directories[index]:
            for other in sources[:index]:
                if other.is_relative_to(source):
                    raise ValueError(f"Reclaim sources overlap: {source} and {other}")
            for destination in destinations:
                if destination.is_relative_to(source):
                    raise ValueError(
                        "Reclaim destination is inside a selected source: "
                        f"{destination}"
                    )
    for index, destination in enumerate(destinations):
        for other in destinations[index + 1 :]:
            if destination.is_relative_to(other) or other.is_relative_to(destination):
                raise ValueError(
                    f"Reclaim destinations overlap: {destination} and {other}"
                )

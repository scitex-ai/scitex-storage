"""The verification population must match actual rsync directory filters."""

import subprocess

import pytest

from scitex_storage._transfer._content_verify import digest_tree
from scitex_storage._transfer._verify import local_tally


def _make_entry(entry, target, kind):
    if kind == "file":
        entry.write_bytes(b"regular file must be copied")
    elif kind == "directory":
        entry.mkdir()
        (entry / "data").write_bytes(b"directory must be excluded")
    else:
        entry.symlink_to(
            target / "payload" if kind == "file-link" else target,
            target_is_directory=kind == "directory-link",
        )


@pytest.mark.parametrize(
    "pattern,relative",
    [
        ("cache/", "cache"),
        ("/cache/", "cache"),
        ("nested/cache/", "nested/cache"),
    ],
)
@pytest.mark.parametrize("kind", ["file", "directory", "file-link", "directory-link"])
def test_directory_only_filter_matches_real_rsync_population(
    tmp_path, pattern, relative, kind
):
    # Arrange
    source = tmp_path / "source space"
    destination = tmp_path / "destination space"
    source.mkdir()
    destination.mkdir()
    (source / "keep.txt").write_bytes(b"retained payload")
    entry = source / relative
    entry.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside"
    target.mkdir()
    (target / "payload").write_bytes(b"link target")
    _make_entry(entry, target, kind)
    # Act
    subprocess.run(
        [
            "rsync",
            "-a",
            "--exclude=" + pattern,
            str(source) + "/",
            str(destination) + "/",
        ],
        check=True,
        capture_output=True,
    )
    baseline = local_tally(str(source), exclude=(pattern,))
    received = local_tally(str(destination))
    expected_content = digest_tree(str(source), exclude=(pattern,))
    received_content = digest_tree(str(destination))
    # Assert
    assert (
        baseline.entry_count,
        baseline.size_bytes,
        expected_content.digests,
        expected_content.unreadable,
        received_content.unreadable,
    ) == (received.entry_count, received.size_bytes, received_content.digests, {}, {})

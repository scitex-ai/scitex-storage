"""The cross-filesystem path of relocate: a real rsync, resumable and source-last.

NO MOCKS (PA-306): calls the real helper with real rsync on temp trees.
"""

from scitex_storage._transfer._relocate import _rsync_move


def test_rsync_move_transfers_directory_content(tmp_path):
    # Arrange
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "a/b.txt").write_bytes(b"payload")
    dst = tmp_path / "elsewhere" / "src"
    dst.parent.mkdir()
    # Act
    _rsync_move(src, dst)
    # Assert
    assert (dst / "a/b.txt").read_bytes() == b"payload"


def test_rsync_move_removes_source_after_copy(tmp_path):
    # Arrange
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "a/b.txt").write_bytes(b"payload")
    dst = tmp_path / "elsewhere" / "src"
    dst.parent.mkdir()
    # Act
    _rsync_move(src, dst)
    # Assert
    assert not src.exists()

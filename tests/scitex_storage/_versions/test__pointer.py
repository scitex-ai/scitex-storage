"""Small plain Git pointers and real isolated repository history/checkout."""

import json
import os
import random
import shutil
import subprocess

import pytest

from scitex_storage._versions import (
    ChunkingConfig,
    VersionStore,
    read_git_pointer,
    render_git_pointer,
    write_git_pointer,
)


def _refusal(action):
    try:
        action()
    except Exception as exc:
        return type(exc).__name__
    return None


def test_pointer_is_small_explicit_and_checkout_verifies_its_metadata(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"scientific data" * 10_000)
    store = VersionStore(tmp_path / "objects")
    # Act
    manifest = store.ingest(source).manifest
    pointer = write_git_pointer(tmp_path / "data.stx.json", manifest)
    parsed = read_git_pointer(pointer)
    restored = store.checkout_pointer(pointer, tmp_path / "restored")
    # Assert
    assert (pointer.stat().st_size < 512, parsed.version_id, restored.read_bytes()) == (
        True,
        manifest.version_id,
        source.read_bytes(),
    )


def test_pointer_creation_never_overwrites_an_existing_file(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"data")
    # Act
    manifest = VersionStore(tmp_path / "objects").ingest(source).manifest
    pointer = tmp_path / "pointer"
    pointer.write_bytes(b"existing user file")
    failure_1 = _refusal(lambda: write_git_pointer(pointer, manifest))
    # Assert
    assert (failure_1, (pointer.read_bytes() == b"existing user file")) == (
        "FileExistsError",
        True,
    )


def test_standard_lfs_pointer_is_not_mislabeled_as_this_format(tmp_path):
    # Arrange
    pointer = tmp_path / "lfs"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:"
        + "a" * 64
        + "\nsize 42\n"
    )
    # Act
    failure_1 = _refusal(lambda: read_git_pointer(pointer))
    # Assert
    assert (failure_1,) == ("IntegrityError",)


def test_pointer_metadata_mismatch_prevents_checkout(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"data")
    store = VersionStore(tmp_path / "objects")
    # Act
    manifest = store.ingest(source).manifest
    value = json.loads(render_git_pointer(manifest))
    value["size"] += 1
    pointer = tmp_path / "pointer"
    pointer.write_text(json.dumps(value))
    target = tmp_path / "restored"
    failure_1 = _refusal(lambda: store.checkout_pointer(pointer, target))
    # Assert
    assert (failure_1, (not target.exists())) == ("IntegrityError", True)


@pytest.mark.skipif(shutil.which("git") is None, reason="Git executable required")
def test_real_git_tracks_only_small_pointers_and_both_versions_restore(tmp_path):
    # Arrange
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Storage fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgSign=false",
                *args,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            env={
                "PATH": os.environ["PATH"],
                "LANG": "C.UTF-8",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
        ).strip()

    git("init", "--initial-branch=main")
    original = random.Random(77).randbytes(800_000)
    source = tmp_path / "large.bin"
    source.write_bytes(original)
    store = VersionStore(
        tmp_path / "objects", chunking=ChunkingConfig(1_024, 4_096, 16_384, 4_096)
    )
    # Act
    first = store.ingest(source)
    pointer = write_git_pointer(repo / "dataset.stx.json", first.manifest)
    git("add", "dataset.stx.json")
    git("commit", "-m", "first data version")
    first_commit = git("rev-parse", "HEAD")
    modified = (
        original[:150_000] + b"inserted instrument metadata" * 9 + original[150_000:]
    )
    source.write_bytes(modified)
    second = store.ingest(source)
    # Explicit edit of this fixture's Git-owned pointer; Storage's creator
    # deliberately has no overwrite/force mode.
    pointer.write_text(render_git_pointer(second.manifest))
    git("add", "dataset.stx.json")
    git("commit", "-m", "second data version")
    old_pointer = json.loads(git("show", f"{first_commit}:dataset.stx.json"))
    current_pointer = json.loads(git("show", "HEAD:dataset.stx.json"))
    first_out = store.checkout(old_pointer["version_sha256"], tmp_path / "version1.bin")
    second_out = store.checkout_pointer(pointer, tmp_path / "version2.bin")
    # Assert
    assert (
        git("rev-list", "--count", "HEAD"),
        git("ls-tree", "--name-only", "HEAD"),
        len(pointer.read_bytes()) < 512,
        first_out.read_bytes() == original,
        second_out.read_bytes() == modified,
        second.reused_bytes / len(original) > 0.90,
        current_pointer["version_sha256"] == second.manifest.version_id,
    ) == ("2", "dataset.stx.json", True, True, True, True, True)

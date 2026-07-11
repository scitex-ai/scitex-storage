#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage quickstart: read-only per-child size + inode inventory."""

import tempfile
from pathlib import Path

import scitex_storage as ss
from scitex_storage._report import format_report


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Two shapes a storage triage must tell apart:
        #   big-data/   — one large file (space hog, few inodes)
        #   many-files/ — many tiny files (inode hog, little space)
        big = root / "big-data"
        big.mkdir()
        (big / "dataset.bin").write_bytes(b"\0" * (200 * 1024))

        many = root / "many-files"
        many.mkdir()
        for i in range(50):
            (many / f"f{i:03d}.txt").write_bytes(b"\0" * 16)

        # scan — read-only, stat-only walk (never reads file contents).
        result = ss.scan(root)
        print("children:", len(result.children))
        print("total files:", result.total_files)

        # Biggest by size vs biggest by inode count are DIFFERENT children.
        biggest = result.by_size()[0]
        most_inodes = result.by_file_count()[0]
        print("biggest child:", biggest.name)
        print("most inodes:", most_inodes.name)
        assert biggest.name == "big-data"
        assert most_inodes.name == "many-files"

        # The human-readable report the CLI prints.
        print(format_report([result]))


if __name__ == "__main__":
    main()

# EOF

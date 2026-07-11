"""scitex-storage quickstart: read-only directory-tree scan + scoring."""

import tempfile
import time
from pathlib import Path

import scitex_storage as ss


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # A small tree: one "hot" file (just written -> low staleness) and
        # one "stale" file (touched to look old -> high score).
        hot = td / "hot.bin"
        hot.write_bytes(b"\0" * 1024)

        stale = td / "old" / "stale.bin"
        stale.parent.mkdir()
        stale.write_bytes(b"\0" * (10 * 1024))
        old_time = time.time() - 400 * 86400  # 400 days ago
        import os

        os.utime(stale, (old_time, old_time))

        # A regenerable dir the scan should skip entirely.
        venv = td / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "site.bin").write_bytes(b"\0" * (5 * 1024))

        # 1. scan — read-only walk + size x staleness scoring.
        result = ss.scan(td)
        print("files scanned:", result.files_scanned)
        print("total size:", result.total_size)
        assert result.files_scanned == 2  # hot.bin + old/stale.bin (.venv skipped)

        # 2. top_candidates — the stale, larger file should score highest.
        top = result.top_candidates(top=5)
        print("top candidate:", top[0].path.name, "score:", top[0].score())
        assert top[0].path.name == "stale.bin"

        # 3. format_text_report — the human-readable CLI output.
        from scitex_storage._report import format_text_report

        print(format_text_report(result))


if __name__ == "__main__":
    main()

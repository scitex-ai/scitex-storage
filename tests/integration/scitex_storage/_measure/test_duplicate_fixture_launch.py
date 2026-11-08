"""Execute the actual protocol fixture through real interpreter path aliases."""

import json
import os
import subprocess
import sys

import pytest

from tests.scitex_storage._measure.test__duplicates import _write_executable


@pytest.mark.parametrize("name", ["python space", "python's space"])
def test_fixture_launcher_preserves_script_and_literal_arguments(tmp_path, name):
    # Arrange -- real Python behind an owned symlink; no PATH interpreter lookup.
    interpreter = tmp_path / name
    interpreter.symlink_to(sys.executable)
    directory = tmp_path / "program path"
    directory.mkdir()
    program = directory / "fclones"
    marker = tmp_path / "must-not-exist"
    arguments = ["two words", "apostrophe's", f"$(touch {marker})", "; exit 29"]
    _write_executable(
        program,
        "import json,sys\nprint(json.dumps([__file__, sys.argv[1:]]))\n",
        interpreter=str(interpreter),
    )
    # Act -- the OS launches the fixture exactly as the production adapter does.
    result = subprocess.run(
        [str(program), *arguments], env=dict(os.environ, PATH=""),
        capture_output=True, text=True, check=True, timeout=10,
    )
    # Assert -- spaces, quotes and shell-looking data retain their literal values.
    assert (json.loads(result.stdout), marker.exists()) == ([str(program), arguments], False)

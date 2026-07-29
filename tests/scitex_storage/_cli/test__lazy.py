"""One broken dependency must not disable unrelated verbs.

MEASURED IN PRODUCTION by scitex-hpc, 2026-07-29, inside the real solver
image on a Spartan compute node: the SIF baked `scitex_ssh` 1.0.1 while this
package requires >=1.2.0, so `sync_dir` was missing. `_cli/__init__.py`
imported `_archive_cmd` eagerly, which pulled `_archive`, which imported
`sync_dir` at module scope -- and `survey` and `find-recipe`, NEITHER OF
WHICH USES SSH, died with exit 1 before argparse ever saw the subcommand.

Two defects, and the tests below pin both:

  * COUPLING -- one skewed sibling package disabled every verb.
  * EXIT 1 -- this package's whole exit-code convention exists so a broken
    verb cannot impersonate an answer. The failure arrived from UPSTREAM of
    the code that owns the contract and fell through to the shell's generic
    1, which a caller cannot distinguish from "not installed" or from a real
    could-not-look verdict.

These run the CLI as a SUBPROCESS on purpose. The defect lives in what
happens at package-import time under a particular sys.path, so importing the
CLI into the test process would not reproduce it -- the test has to own the
interpreter to own the path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import scitex_storage

#: Reserved: the verb exists but its module could not be imported.
EXIT_VERB_UNAVAILABLE = 20
#: `survey` refuses a verdict on a tree it read zero files from.
EXIT_SURVEY_COULD_NOT_LOOK = 13

_PKG_PARENT = str(Path(scitex_storage.__file__).resolve().parents[1])


def _stale_ssh_path(tmp_path) -> str:
    """A `scitex_ssh` that is importable but lacks `sync_dir`.

    Reproduces the SIF's baked 1.0.1 exactly: the name resolves, the symbol
    does not. A merely ABSENT package would exercise a different code path
    (ModuleNotFoundError), and the production failure was an ImportError on
    a symbol -- a subtler thing that a "just check it imports" guard misses.
    """
    shim = tmp_path / "shim"
    (shim / "scitex_ssh").mkdir(parents=True)
    (shim / "scitex_ssh" / "__init__.py").write_text(
        "class SSHResult:\n    pass\n\n\ndef exec_remote(*a, **k):\n"
        "    raise NotImplementedError\n"
    )
    return str(shim)


def _run(tmp_path, *args):
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": _stale_ssh_path(tmp_path) + ":" + _PKG_PARENT,
    }
    return subprocess.run(
        [sys.executable, "-m", "scitex_storage", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_a_broken_sibling_dependency_does_not_disable_an_unrelated_verb(tmp_path):
    # `survey` does not use SSH. Before the fix this exited 1 with an
    # ImportError from _archive.py and the verb never ran at all.
    # Arrange
    target = tmp_path / "empty"
    target.mkdir()

    # Act
    result = _run(tmp_path, "survey", str(target), "--json")

    # Assert
    assert result.returncode == EXIT_SURVEY_COULD_NOT_LOOK


def test_an_unloadable_verb_exits_with_the_reserved_code_not_one(tmp_path):
    # `archive` genuinely needs the broken dependency, so it MUST fail -- but
    # in the declared shape. 20 says TOOL BROKEN and is deliberately outside
    # every verb's verdict range (find-recipe 10/11, survey 12/13), so a
    # caller can never read an environment defect as an answer.
    # Arrange
    src = tmp_path / "src"
    src.mkdir()

    # Act
    result = _run(tmp_path, "archive", str(src), "nas2:/tmp/dest")

    # Assert
    assert result.returncode == EXIT_VERB_UNAVAILABLE


def test_the_unavailable_verb_names_the_package_that_broke_it(tmp_path):
    # A refusal that does not say what to check is a dead end. The operator
    # needs the offending module named, because the usual cause is a stale
    # copy of a sibling package winning the import inside a container image.
    # Arrange
    src = tmp_path / "src"
    src.mkdir()

    # Act
    result = _run(tmp_path, "archive", str(src), "nas2:/tmp/dest")

    # Assert
    assert "scitex_ssh" in result.stderr


def test_help_still_lists_verbs_when_a_dependency_is_broken(tmp_path):
    # THE DIAGNOSTIC PROPERTY. `--help` answers from the verb registry
    # WITHOUT importing anything, so the CLI can still say what exists at
    # exactly the moment its dependencies are broken -- which is when someone
    # most needs to be told.
    # Arrange
    expected_verb = "survey"

    # Act
    result = _run(tmp_path, "--help")

    # Assert
    assert expected_verb in result.stdout

"""Pytest fixtures and rootdir marker for this package.

An empty conftest.py at tests/ is the canonical SciTeX
convention (audit-project PS208) — it pins the pytest
rootdir and gives downstream fixtures a home.

It is no longer empty, and the reason is worth reading before anyone
removes the marker below.

WHAT WENT WRONG. `scan` shells out to `fd` (see `_system_deps.py`), and
`fd` is declared there in the apt federation. The shared `pytest-matrix`
CI job does not install it. That mismatch cost nothing for months
because GitHub's hosted ubuntu image happened to ship `fd`, so the tests
that need it simply passed. On 2026-08-15 the image no longer had it and
those tests went from passing to erroring — with no code change, and
sixteen minutes apart on the same branch.

The green run reads "964 passed, 2 skipped". TWO skips. So these tests
were never skipping on a missing binary; they were passing on an
incidental one. A dependency that is declared, real, and enforced at
runtime was nonetheless being satisfied by luck.

WHY A SKIP IS NOT A WEAKENED GATE HERE. `.github/workflows/ci.yml`'s
`fd-fclones-integration` job installs fd + fclones and reruns THE WHOLE
SUITE. Anything skipped on the matrix still runs for real there, so
marking these costs no coverage — it moves the coverage to the job that
can actually provide it, and makes the matrix honest about what it did
not exercise. `ci.yml` has documented this as the intended design since
the job was written ("the tests that exercise them for real stay skipped
there"); it was simply never implemented.

WHY A MARKER, when `fclones` uses a local `requires_fclones = skipif(...)`
in each file (`test__duplicates.py`, `_cli/test__duplicates_cmd.py`).
That idiom is right at two files and wrong at eight: the fd-dependent
tests span far more modules, and copying a four-line preamble into each
invites the copies to drift apart. A registered marker keeps one
definition, one skip reason, and one place to change the binary lookup.
The fclones idiom stays as it is — this is not a call to convert it.
"""

from __future__ import annotations

import shutil

import pytest

#: Debian/Ubuntu package `fd-find` installs the binary as `fdfind`, NOT
#: as `fd`; elsewhere it is `fd`. `_scan.py`'s `_fd_binary()` checks both
#: names, so this must check both too — a single-name probe would skip
#: the suite on exactly the platform where fd IS installed, which is a
#: worse failure than the one it was written to prevent (it would be
#: silent, and it would look like coverage).
_HAVE_FD = bool(shutil.which("fd") or shutil.which("fdfind"))

_SKIP_REASON = (
    "requires the `fd` binary on PATH (apt: fd-find). Not a gap in "
    "coverage: these run for real in the `fd-fclones-integration` CI "
    "job, which installs fd + fclones and reruns the whole suite."
)


def pytest_configure(config):
    """Register `requires_fd` so `--strict-markers` accepts it."""
    config.addinivalue_line(
        "markers",
        "requires_fd: test shells out to `fd`; skipped when fd is absent, "
        "and run for real by the fd-fclones-integration CI job.",
    )


def pytest_collection_modifyitems(config, items):
    """Skip `requires_fd` tests when neither `fd` nor `fdfind` is on PATH.

    Deliberately keyed on the MARKER rather than on catching
    `MissingSystemDependencyError` at runtime. Converting a live
    exception into a skip would also swallow the case where fd IS
    installed and `scan` raises for some other reason — turning a real
    regression into a green run. The marker says "this test is known to
    need fd" up front, which is a claim someone wrote down and can be
    checked, rather than a verdict inferred from a failure.
    """
    if _HAVE_FD:
        return
    skip_fd = pytest.mark.skip(reason=_SKIP_REASON)
    for item in items:
        if "requires_fd" in item.keywords:
            item.add_marker(skip_fd)

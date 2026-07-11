#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_system_deps.py
"""scitex-storage's system (non-apt / cargo-or-release-binary) dependency provider.

Two verbs shell out to Rust CLIs for their hot paths instead of
reimplementing a directory walk or a size+hash duplicate pass in Python
(see ``_scan.py`` and ``_duplicates.py``'s module docstrings for the full
rationale):

* ``fd`` (fd-find) — ``scan``'s directory traversal, replacing ``os.walk``.
  Packaged as ``fd-find`` on Debian/Ubuntu (apt package name differs from
  the binary name: it installs as ``fdfind``, not ``fd`` — ``_scan.py``'s
  ``_fd_binary()`` checks both names).
* ``fclones`` — ``find-duplicates``'s size+hash duplicate detection,
  replacing a hand-rolled ``hashlib`` pass. **Not** packaged in Debian/
  Ubuntu's default apt repos as of this writing (unlike ``fd-find``);
  install via ``cargo install fclones``, Homebrew, or a prebuilt release
  binary — see https://github.com/pkolaczk/fclones#installation.

Registered under the same ``scitex_dev.system_deps`` entry-point federation
every leaf uses (see ``scitex_dev.system_deps`` and e.g.
``scitex_writer._core._system_deps`` / ``scitex_dev._system_deps`` for the
same pattern), so ``scitex-dev ecosystem system-deps`` aggregates it
fleet-wide. Neither binary is required to *install* scitex-storage (no
PyPI package for either exists, and pip has no notion of a system binary
dependency) — ``fd`` is a hard *runtime* dependency of ``scan``, and
``fclones`` of ``find-duplicates``, only. A missing binary raises
``MissingSystemDependencyError`` with install instructions rather than a
build failure or a silent slow fallback.

``fclones`` has no apt package, so ``SystemDepSpec.apt_repo`` cannot express
its install; that gap is intentional -- ``apt_repo`` only covers "extra apt
source needed before an apt install," not "not on apt at all." Callers that
walk this federation for an apt-based image build must still special-case
``fclones`` (e.g. a ``cargo install fclones`` step) until/unless it lands in
Debian's repos.
"""

from __future__ import annotations

# (package, purpose). fd-find is a real Debian/Ubuntu apt package name;
# fclones is not on apt as of this writing (see module docstring) but is
# still declared here as the canonical, fleet-wide record of "scitex-storage
# needs this system binary" -- an apt-only aggregator must special-case it.
_PACKAGES: list[tuple[str, str]] = [
    (
        "fd-find",
        "fast directory traversal for `scitex-storage scan` (binary: `fdfind` "
        "on Debian/Ubuntu, `fd` elsewhere) -- replaces os.walk",
    ),
    (
        "fclones",
        "fast size+hash duplicate-file detection for `scitex-storage "
        "find-duplicates` (no Debian/Ubuntu apt package -- install via "
        "`cargo install fclones`, Homebrew, or a release binary) -- "
        "replaces a hashlib pass",
    ),
]

#: Package names, in the order declared above (the SSoT list).
PACKAGE_NAMES: list[str] = [pkg for pkg, _ in _PACKAGES]


def provide():
    """Return this leaf's system deps for the ``scitex_dev.system_deps`` group.

    ``SystemDepSpec`` is imported lazily from ``scitex_dev`` so this module
    stays importable even where ``scitex-dev`` isn't installed -- the
    aggregator that calls ``provide()`` runs in an environment that provides
    ``scitex_dev``; a bare ``scitex-storage`` install never needs to.
    """
    from scitex_dev.system_deps import SystemDepSpec

    return [
        SystemDepSpec(package=pkg, purpose=purpose, provider="scitex-storage")
        for pkg, purpose in _PACKAGES
    ]


def _main() -> int:
    print("\n".join(PACKAGE_NAMES))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())

# EOF

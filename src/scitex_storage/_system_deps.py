#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_system_deps.py
"""scitex-storage's system (non-pip) dependency provider.

Three binaries. Two are Rust CLIs the hot paths shell out to instead of
reimplementing a directory walk or a size+hash duplicate pass in Python
(see ``_scan.py`` and ``_duplicates.py``'s module docstrings for the full
rationale); the third is the transport ``archive``/``restore`` are built on:

* ``fd`` (fd-find) — ``scan``'s directory traversal, replacing ``os.walk``.
  Packaged as ``fd-find`` on Debian/Ubuntu (apt package name differs from
  the binary name: it installs as ``fdfind``, not ``fd`` — ``_scan.py``'s
  ``_fd_binary()`` checks both names).
* ``fclones`` — ``find-duplicates``'s size+hash duplicate detection,
  replacing a hand-rolled ``hashlib`` pass. **Not** packaged in Debian/
  Ubuntu's default apt repos as of this writing (unlike ``fd-find``);
  install via ``cargo install fclones``, Homebrew, or a prebuilt release
  binary — see https://github.com/pkolaczk/fclones#installation.
* ``rsync`` — the transport under ``archive``/``restore``. Neither verb
  talks to a network itself: both delegate to scitex-ssh's ``sync_dir``,
  which is "a thin, policy-free wrapper over ``rsync -a``" over ssh. So the
  LOCAL rsync binary is as hard a runtime dependency of ``archive`` as
  ``fd`` is of ``scan`` — scitex-ssh is the declared PyPI dependency, but it
  is an ADAPTER, and the thing it adapts needs declaring too. Ordinary apt
  package (``apt install rsync``), unlike ``fclones``.

  Missed until 2026-07-17, and worth recording why rather than just fixing:
  the dependency is INVISIBLE FROM THIS PACKAGE'S SOURCE. Nothing here
  spawns rsync; ``_archive.py`` calls ``sync_dir()``, a normal Python
  function from a normal declared dependency, and the subprocess happens one
  package away. A cross-package import gate cannot catch that either — it
  proves ``import scitex_ssh`` resolves, which it does, right up until the
  binary underneath is missing. Found by dogfooding (``archive`` could not
  run in the container scitex-storage ships to), not by any check we own.

Registered under the same ``scitex_dev.system_deps`` entry-point federation
every leaf uses (see ``scitex_dev.system_deps`` and e.g.
``scitex_writer._core._system_deps`` / ``scitex_dev._system_deps`` for the
same pattern), so ``scitex-dev ecosystem system-deps`` aggregates it
fleet-wide. No binary here is required to *install* scitex-storage (pip has
no notion of a system binary dependency) — each is a hard *runtime*
dependency of its own verb, and of nothing else: ``fd`` of ``scan``,
``fclones`` of ``find-duplicates``, ``rsync`` of ``archive``/``restore``. A
missing binary raises ``MissingSystemDependencyError`` with install
instructions rather than a build failure or a silent slow fallback.

``fclones`` IS DELIBERATELY NOT IN THIS FEDERATION, and the reason is worth
reading before anyone adds it back.

It was here until 2026-07-29, carrying a prose caveat that said "no apt
package -- callers walking this federation for an apt-based image build must
special-case it." That caveat was accurate, prominent, repeated three times
in this module, and USELESS, because a consumer reads the LIST, not the
prose around it. The moment scitex-storage was actually installed into the
:scitex image layer, the aggregator fed ``fclones`` straight into
``apt-get install`` and the build died with ``E: Unable to locate package
fclones``.

THE BLAST RADIUS WAS THE INSTRUCTIVE PART: apt aborts the WHOLE transaction
on one unknown name, so biber, chktex, latexmk and every texlive package
silently did not install either. The build then failed four lines later on
``pdflatex: not found`` -- an error pointing at LaTeX, in a layer nobody had
changed. A declaration that is wrong in one entry took down twenty that were
right, and disguised itself as someone else's problem on the way out.

``SystemDepSpec`` has exactly four fields -- ``package``, ``purpose``,
``provider``, ``apt_repo`` -- and ``apt_repo`` means "an extra apt SOURCE is
needed first", not "this does not come from apt at all". So the type cannot
represent a non-apt dependency, which makes this federation apt-shaped by
construction. Putting a cargo-only binary in it and annotating the exception
in English is asking every consumer to re-derive a rule that the data
structure could not carry. That is a written warning where a mechanism was
needed, which is the failure mode this codebase keeps finding elsewhere.

WHERE THE fclones REQUIREMENT LIVES INSTEAD. Removing it from the apt list
must NOT delete the fact that we need it -- that would buy a working build
by making the aggregate lie by omission, trading a loud failure for a silent
gap. So it moves to ``NON_APT_REQUIREMENTS`` below: still declared, still
machine-readable, simply not fed to apt. It is additionally enforced at
runtime, where ``find-duplicates`` raises ``MissingSystemDependencyError``
with install instructions, and pinned by
``tests/scitex_storage/_cli/test__duplicates_cmd.py`` skipping on
``shutil.which("fclones")``.

THIS IS A STOPGAP, NOT A DESIGN. Until ``SystemDepSpec`` can express a
channel (cargo / github-release / manual), every leaf with a non-apt tool
faces the same forced choice between detonating an apt transaction and
lying about its dependencies. When that field lands, these entries move back
into the federation and ``NON_APT_REQUIREMENTS`` goes away. Raised with
scitex-dev, who are scoping it deliberately rather than deciding a federated
schema change in a hurry.
"""

from __future__ import annotations

# (package, purpose). EVERY ENTRY HERE MUST BE INSTALLABLE BY `apt-get
# install <package>` ON A STOCK DEBIAN/UBUNTU. This federation has no field
# for any other channel, so an entry that is not an apt package is not an
# exception to be annotated -- it is a broken entry that aborts the whole
# apt transaction for every other package in the aggregate. See the module
# docstring for the build this cost.
_PACKAGES: list[tuple[str, str]] = [
    (
        "fd-find",
        "fast directory traversal for `scitex-storage scan` (binary: `fdfind` "
        "on Debian/Ubuntu, `fd` elsewhere) -- replaces os.walk",
    ),
    (
        "rsync",
        "the transport under `scitex-storage archive` / `restore` -- both "
        "delegate to scitex-ssh's `sync_dir`, a wrapper over `rsync -a` "
        "over ssh, so the LOCAL rsync binary is required (the remote host "
        "needs one too, but that fails ssh-side). Ordinary apt package.",
    ),
]

#: Package names, in the order declared above (the SSoT list).
PACKAGE_NAMES: list[str] = [pkg for pkg, _ in _PACKAGES]


# (binary, purpose, install). REQUIRED SYSTEM BINARIES THAT APT CANNOT
# SUPPLY.
#
# THIS EXISTS SO THAT REMOVING fclones FROM THE APT LIST DOES NOT DELETE THE
# FACT THAT WE NEED IT. Dropping the declaration outright would buy a working
# build by making the aggregate LIE BY OMISSION -- converting a loud failure
# into a silent gap, which is a strictly worse trade and the exact move this
# codebase argues against everywhere else. The apt list must not contain it;
# the SSoT must not forget it. Those are compatible only if the fact lives
# somewhere machine-readable that the apt path does not walk.
#
# Deliberately DATA, not prose. The previous version recorded this in the
# module docstring, three times, prominently -- and a consumer read the list
# and fed `fclones` to `apt-get install` anyway, because prose is not an
# interface. Anything that must survive being consumed by a program has to be
# a value that program can read.
#
# This is a STOPGAP with a known end state: when `SystemDepSpec` grows a
# channel field (cargo / github-release / manual), these entries move back
# into the federation proper and this constant goes away. Until then, a leaf
# with a non-apt tool is forced to choose between detonating an apt
# transaction and lying about its dependencies; this is the least-bad third
# option, not a design.
NON_APT_REQUIREMENTS: list[tuple[str, str, str]] = [
    (
        "fclones",
        "fast size+hash duplicate-file detection for `scitex-storage "
        "find-duplicates` -- replaces a hand-rolled hashlib pass",
        "cargo install fclones  (or Homebrew, or a prebuilt release binary "
        "from https://github.com/pkolaczk/fclones#installation)",
    ),
]

#: Binary names from NON_APT_REQUIREMENTS, for callers that only need the set.
NON_APT_BINARIES: list[str] = [binary for binary, _, _ in NON_APT_REQUIREMENTS]


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

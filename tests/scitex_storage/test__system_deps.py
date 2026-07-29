"""The apt federation must contain only things apt can install.

MEASURED 2026-07-29. `fclones` was declared in the apt system-deps
federation with a prose caveat saying it has no apt package and that
consumers must special-case it. The caveat was accurate, repeated three
times in the module, and useless: a consumer reads the LIST, not the prose
around it. When scitex-storage was first installed into the :scitex image
layer, the aggregator fed `fclones` to `apt-get install` and the build died
with `E: Unable to locate package fclones`.

THE BLAST RADIUS IS WHY THIS HAS A TEST. apt aborts the WHOLE transaction on
one unknown name, so biber, chktex, latexmk and every texlive package
silently did not install either, and the build failed four lines later on
`pdflatex: not found` -- an error pointing at a layer nobody had touched.
One wrong entry took down twenty right ones and disguised itself as someone
else's problem.

`SystemDepSpec` has four fields -- package, purpose, provider, apt_repo --
and apt_repo means "an extra apt SOURCE is needed first", not "this does not
come from apt". The type cannot represent a non-apt dependency, so the
federation is apt-shaped by construction and a written warning was standing
in for a mechanism. This is the mechanism.
"""

from __future__ import annotations

from scitex_storage._system_deps import (
    NON_APT_BINARIES,
    NON_APT_REQUIREMENTS,
    PACKAGE_NAMES,
    provide,
)

#: Binaries known to have no Debian/Ubuntu apt package. Adding one of these
#: to the apt federation aborts the entire apt transaction for every other
#: package in the fleet-wide aggregate.
KNOWN_NON_APT = {"fclones"}


def test_the_apt_federation_declares_no_known_non_apt_package():
    # THE REGRESSION THIS EXISTS FOR. Re-adding fclones here is not an
    # exception to be annotated -- it is an entry that breaks the build for
    # every other package in the aggregate, including ones from other repos.
    # Arrange
    declared = set(PACKAGE_NAMES)

    # Act
    offenders = declared & KNOWN_NON_APT

    # Assert
    assert offenders == set()


def test_the_non_apt_requirement_is_still_declared_somewhere():
    # REMOVING fclones FROM apt MUST NOT DELETE THE FACT THAT WE NEED IT.
    # Dropping the declaration outright would buy a working build by making
    # the aggregate lie by omission -- a silent gap in place of a loud
    # failure. This pins that the fact survived the move.
    # Arrange
    expected = "fclones"

    # Act
    declared_non_apt = set(NON_APT_BINARIES)

    # Assert
    assert expected in declared_non_apt


def test_every_non_apt_requirement_carries_an_install_instruction():
    # A declaration a reader cannot act on is only marginally better than no
    # declaration. Each entry must say how to actually get the binary.
    # Arrange
    entries = NON_APT_REQUIREMENTS

    # Act
    missing_install = [b for b, _, install in entries if not install.strip()]

    # Assert
    assert missing_install == []


def test_the_apt_and_non_apt_sets_do_not_overlap():
    # A binary claimed on both channels means one of the two declarations is
    # wrong, and the apt one would be the one that detonates.
    # Arrange
    apt = set(PACKAGE_NAMES)

    # Act
    overlap = apt & set(NON_APT_BINARIES)

    # Assert
    assert overlap == set()


def test_provide_emits_only_apt_installable_packages():
    # POSITIVE CONTROL on the federation entry point itself, not just on the
    # module constant. `provide()` is what the aggregator actually calls, so
    # asserting on PACKAGE_NAMES alone would leave the real surface untested.
    # Arrange
    specs = provide()

    # Act
    emitted = {s.package for s in specs}

    # Assert
    assert emitted & KNOWN_NON_APT == set()

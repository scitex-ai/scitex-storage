#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_regenerable.py
"""Is this tree REGENERABLE -- rebuildable from a recipe that still exists?

This answers a different question from :mod:`_classify`, which asks whether
a tree *can physically move*. Here we ask whether deleting it loses
anything, which is what actually licenses reclaiming space or inodes.

Paid for by Spartan punim0264 at 97% INODE usage, where inode exhaustion
silently FAILs every writing job. Measured on a representative capsule:
83,667 inodes total, of which the runtime environment was ~97%
(``rsandbox`` 54,891 + ``mamba`` 20,428) while the scientific ``results``
directory was 24. The output is a rounding error; the environment is the
whole cost.

TWO RULES, both learned the expensive way.

**DETECT BY STRUCTURE, NEVER BY NAME.** The environment directory name
differs per capsule -- ``rsandbox``, ``mamba``, ``pylibs``, ``myenv``,
``mmroot``, ``mm_root``, ``venv``, ``renv`` were all observed in one
project. A name-based rule under-matches and leaves most of the inodes
in place, which is the failure that looks like success: it runs, it
deletes something, and the quota does not move. Structure does not drift:
a virtualenv has ``pyvenv.cfg`` whatever you called the directory.

**REGENERABLE IS A CLAIM ABOUT THE RECIPE, NOT ABOUT THE ARTIFACT.** An
environment is only regenerable if the spec that rebuilds it still
exists. A ``site-packages`` tree with no ``environment.yml``,
``requirements.txt``, ``pyproject.toml`` or ``renv.lock`` anywhere above
it is not "regenerable" -- it is simply the only copy, and deleting it
destroys the ability to re-run the analysis. Calling that regenerable is
how a cleanup tool eats a result it cannot give back. So the recipe is
probed separately and its absence downgrades the verdict rather than
being assumed.

The third state is mandatory for the same reason it is elsewhere in this
package: a directory we could not read must not be reported as "nothing
of value here". The failure mode always produces the convenient answer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: The tree is rebuildable: an environment was recognised by structure AND
#: a spec that can rebuild it was found.
REGENERABLE = "regenerable"
#: The tree is a CACHE: rebuildable with NO spec required, because the
#: recipe is the network rather than a file. Added 2026-07-28 after
#: paper-scitex-clew ran this detector against 10 real capsule trees and
#: found its largest consumers -- `pylibs`, `mamba`, `.uvcache`, tens of
#: thousands of inodes each -- were neither environments nor
#: non-environments. They are caches, and requiring a spec of a cache is
#: exactly as wrong as not requiring one of an environment: a package
#: cache with no environment.yml is still 100% disposable.
CACHE = "cache"
#: The tree is not safe to discard -- either it is not an environment, or
#: it is one whose rebuild recipe is missing.
NOT_REGENERABLE = "not-regenerable"
#: The question was not answered. NOT a synonym for "nothing here".
COULD_NOT_LOOK = "could-not-look"

_VERDICTS = (REGENERABLE, CACHE, NOT_REGENERABLE, COULD_NOT_LOOK)

#: Why a look failed. `could-not-look` alone conflated two states that a
#: deletion sweep must treat OPPOSITELY: a file is routine and skippable,
#: while an ABSENT path means the caller's inventory is stale -- something
#: already moved or was deleted -- and the right response is to stop and
#: re-measure rather than continue. Reported by paper-scitex-clew 2026-07-28
#: after all three cases returned character-for-character identical strings.
REASON_IS_FILE = "not-a-directory"
REASON_ABSENT = "absent"
REASON_UNREADABLE = "unreadable"

#: Freedesktop's cache marker. Any tool that writes it is DECLARING the
#: tree disposable, which is a stronger signal than anything we could
#: infer -- it is the author's own statement of intent.
CACHEDIR_TAG = "CACHEDIR.TAG"

#: Structural markers that identify a runtime environment, mapped to the
#: ecosystem they belong to. Each is a path RELATIVE to the candidate
#: directory. These are properties of the tool that built the tree, not of
#: what a human decided to call it.
ENVIRONMENT_MARKERS: dict[str, str] = {
    "pyvenv.cfg": "python-venv",
    "conda-meta": "conda",
    "renv/library": "r-renv",
    "node_modules/.package-lock.json": "node",
}

#: Glob-ish structural markers needing a wildcard component, checked after
#: the exact ones. ``lib/python3.11/site-packages`` cannot be spelled
#: without knowing the version.
_SITE_PACKAGES_PARENTS = ("lib", "lib64")

#: Files that constitute a rebuild recipe, mapped to the ecosystem they
#: can rebuild. A spec only counts for the ecosystem it actually serves:
#: a ``requirements.txt`` does not rebuild an renv library.
SPEC_FILES: dict[str, tuple[str, ...]] = {
    "python-venv": (
        "requirements.txt",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "setup.py",
    ),
    "conda": (
        "environment.yml",
        "environment.yaml",
        "conda-lock.yml",
    ),
    # A `pip install --target` tree is rebuilt by the same recipes as a
    # venv -- it is an INSTALL LAYOUT, not a different ecosystem. Kept as
    # its own key so the verdict names which layout was found, since the
    # two are reclaimed and rebuilt differently in practice.
    "python-target": (
        "requirements.txt",
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "setup.py",
    ),
    "r-renv": ("renv.lock", "DESCRIPTION"),
    "node": ("package.json",),
}


@dataclass(frozen=True)
class RegenerableVerdict:
    """A fixed-shape answer, so a caller never guesses which key exists.

    Every field is present on every call. ``verdict`` is three-valued;
    ``ecosystem`` and ``spec_path`` are ``None`` when not determined,
    which is distinct from being empty.
    """

    path: str
    verdict: str
    ecosystem: str | None
    marker: str | None
    spec_path: str | None
    evidence: str
    #: Only set when verdict is COULD_NOT_LOOK: which of the not-heres it
    #: was. See REASON_* -- a file and an absent path are routine and
    #: alarming respectively, and a caller must be able to tell them apart.
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.verdict not in _VERDICTS:
            raise ValueError(
                f"{self.path}: verdict {self.verdict!r} is not one of {_VERDICTS}"
            )
        if not self.evidence.strip():
            raise ValueError(
                f"{self.path}: refusing a verdict with no evidence -- a "
                f"verdict that cannot be audited is not a measurement"
            )
        if self.verdict == REGENERABLE and not self.spec_path:
            raise ValueError(
                f"{self.path}: refusing REGENERABLE with no spec_path -- "
                f"'rebuildable' without naming the recipe is a guess, and "
                f"deleting on a guess is unrecoverable"
            )
        # CACHE is deliberately EXEMPT from that rule, and only CACHE. A
        # cache's recipe is the network, so demanding a spec file would
        # refuse trees that are unambiguously disposable. The exemption is
        # narrow on purpose: it is granted by a structural marker the
        # writing tool chose (CACHEDIR.TAG, a conda pkgs-only root), never
        # by the absence of evidence.
        if self.verdict == COULD_NOT_LOOK and not self.reason:
            raise ValueError(
                f"{self.path}: refusing COULD_NOT_LOOK with no reason -- "
                f"'a file, skip it' and 'this path is gone, your inventory "
                f"is stale' need opposite reactions from a deletion sweep, "
                f"and one string for both is how they get conflated"
            )


def detect_environment(path: str) -> tuple[str | None, str | None]:
    """Identify a runtime environment at ``path`` by STRUCTURE.

    Returns ``(ecosystem, marker)``, or ``(None, None)`` when nothing
    structural matched. Raises nothing: an unreadable path simply does
    not match, and the caller distinguishes that via
    :func:`is_regenerable`, which probes readability first.

    The directory NAME is never consulted. That is the whole point.
    """
    for marker, ecosystem in ENVIRONMENT_MARKERS.items():
        if os.path.exists(os.path.join(path, marker)):
            return ecosystem, marker

    # site-packages needs a wildcard for the python version component.
    for libdir in _SITE_PACKAGES_PARENTS:
        base = os.path.join(path, libdir)
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            candidate = os.path.join(base, entry, "site-packages")
            if os.path.isdir(candidate):
                return "python-venv", os.path.join(libdir, entry, "site-packages")

    # `pip install --target` flat tree: packages unpacked directly, so
    # there is no pyvenv.cfg and no lib/pythonX/site-packages -- but PEP
    # 376 requires a `<name>-<version>.dist-info/` beside each package,
    # which is exactly as structural as pyvenv.cfg. Measured 2026-07-28:
    # this shape (`pylibs`, `pylib`, `sdlib`) is the most common
    # environment in the punim0264 capsule corpus and was invisible to
    # every check above.
    try:
        for entry in os.listdir(path):
            if entry.endswith(".dist-info") and os.path.isdir(
                os.path.join(path, entry)
            ):
                return "python-target", entry
    except OSError:
        pass
    return None, None


def detect_cache(path: str) -> tuple[str | None, str | None]:
    """Identify a CACHE at ``path`` by structure. Returns ``(kind, marker)``.

    A cache differs from an environment in the one way that matters here:
    it needs no spec to be regenerable, because the recipe is the network.
    Both markers are the writing tool's own declaration rather than our
    inference, which is why they are trustworthy enough to skip the spec
    gate:

    * ``CACHEDIR.TAG`` -- the freedesktop standard. A tool that writes it
      is stating the tree is disposable.
    * a conda/mamba root containing ONLY ``pkgs/`` -- a package cache, not
      an environment and not an install root. Measured on real trees
      (`mamba`, `mmroot`): they hold `pkgs` and nothing else.
    """
    if os.path.exists(os.path.join(path, CACHEDIR_TAG)):
        return "cachedir-tag", CACHEDIR_TAG
    try:
        entries = [e for e in os.listdir(path) if not e.startswith(".")]
    except OSError:
        return None, None
    if entries == ["pkgs"] and os.path.isdir(os.path.join(path, "pkgs")):
        return "conda-pkgs", "pkgs"
    return None, None


def find_spec(start: str, ecosystem: str, stop_at: str | None = None) -> str | None:
    """Search ``start`` and its ancestors for a recipe rebuilding ``ecosystem``.

    Walks upward because the convention is a spec beside the project, not
    inside the environment it produced: ``capsule/environment.yml`` next
    to ``capsule/mamba/``. ``stop_at`` bounds the walk so a spec belonging
    to an unrelated parent project is not credited to this tree -- without
    it, one ``pyproject.toml`` at the top of a shared filesystem would
    declare every environment beneath it regenerable.
    """
    candidates = SPEC_FILES.get(ecosystem, ())
    current = os.path.abspath(start)
    boundary = os.path.abspath(stop_at) if stop_at else None
    while True:
        for name in candidates:
            found = os.path.join(current, name)
            if os.path.isfile(found):
                return found
        if boundary is not None and current == boundary:
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def is_regenerable(path: str, stop_at: str | None = None) -> RegenerableVerdict:
    """Decide whether ``path`` may be discarded and rebuilt.

    The order is deliberate: readability is probed FIRST, so an unreadable
    directory becomes ``COULD_NOT_LOOK`` rather than falling through the
    structural checks and emerging as "not an environment" -- which reads
    as a clean answer and is not one.
    """
    if not os.path.exists(path):
        return RegenerableVerdict(
            path=path,
            verdict=COULD_NOT_LOOK,
            ecosystem=None,
            marker=None,
            spec_path=None,
            reason=REASON_ABSENT,
            evidence=(
                "path does not exist -- this is NOT the routine 'skip a "
                "file' case: the caller's inventory is stale, something "
                "already moved or was deleted, and a sweep should stop and "
                "re-measure rather than continue"
            ),
        )
    if not os.path.isdir(path):
        return RegenerableVerdict(
            path=path,
            verdict=COULD_NOT_LOOK,
            ecosystem=None,
            marker=None,
            spec_path=None,
            reason=REASON_IS_FILE,
            evidence=(
                "not a directory -- routine, nothing to classify here; a "
                "sweep may skip it and continue"
            ),
        )
    try:
        os.listdir(path)
    except OSError as exc:
        return RegenerableVerdict(
            path=path,
            verdict=COULD_NOT_LOOK,
            ecosystem=None,
            marker=None,
            spec_path=None,
            reason=REASON_UNREADABLE,
            evidence=f"unreadable ({exc.strerror}) -- absence of evidence is not evidence of absence",
        )

    # CACHE is checked BEFORE environment: a conda root holding only
    # `pkgs/` would otherwise be missed entirely, and a tree carrying both
    # CACHEDIR.TAG and an env marker is one its author declared disposable.
    cache_kind, cache_marker = detect_cache(path)
    if cache_kind is not None:
        return RegenerableVerdict(
            path=path,
            verdict=CACHE,
            ecosystem=cache_kind,
            marker=cache_marker,
            spec_path=None,
            evidence=(
                f"CACHE ({cache_kind}, marker {cache_marker}) -- regenerable "
                f"with NO spec required, because the recipe is the network "
                f"rather than a file. The marker is the writing tool's own "
                f"declaration that this tree is disposable, not an inference "
                f"of ours."
            ),
        )

    ecosystem, marker = detect_environment(path)
    if ecosystem is None:
        return RegenerableVerdict(
            path=path,
            verdict=NOT_REGENERABLE,
            ecosystem=None,
            marker=None,
            spec_path=None,
            evidence=(
                "no structural environment marker found "
                f"(looked for {', '.join(ENVIRONMENT_MARKERS)}, site-packages)"
            ),
        )

    spec = find_spec(path, ecosystem, stop_at=stop_at)
    if spec is None:
        return RegenerableVerdict(
            path=path,
            verdict=NOT_REGENERABLE,
            ecosystem=ecosystem,
            marker=marker,
            spec_path=None,
            evidence=(
                f"{ecosystem} environment (marker {marker}) but NO rebuild spec "
                f"found in it or its ancestors (looked for "
                f"{', '.join(SPEC_FILES.get(ecosystem, ()))}) -- without a recipe "
                f"this is the only copy, not a cache"
            ),
        )

    return RegenerableVerdict(
        path=path,
        verdict=REGENERABLE,
        ecosystem=ecosystem,
        marker=marker,
        spec_path=spec,
        evidence=(
            f"{ecosystem} environment (marker {marker}), rebuildable from {spec}"
        ),
    )

# EOF

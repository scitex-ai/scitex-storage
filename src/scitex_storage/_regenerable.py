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
#: The tree is not safe to discard -- either it is not an environment, or
#: it is one whose rebuild recipe is missing.
NOT_REGENERABLE = "not-regenerable"
#: The question was not answered: the path is unreadable, absent, or not a
#: directory. NOT a synonym for "nothing here".
COULD_NOT_LOOK = "could-not-look"

_VERDICTS = (REGENERABLE, NOT_REGENERABLE, COULD_NOT_LOOK)

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
    if not os.path.isdir(path):
        return RegenerableVerdict(
            path=path,
            verdict=COULD_NOT_LOOK,
            ecosystem=None,
            marker=None,
            spec_path=None,
            evidence="not a directory, or absent -- nothing was measured",
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
            evidence=f"unreadable ({exc.strerror}) -- absence of evidence is not evidence of absence",
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

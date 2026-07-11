#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage: research-data storage triage.

Current scope (see README for the full roadmap): a read-only directory-tree
``scan`` that inventories the biggest space and inode (file-count)
consumers per top-level child of a root; ``images prune`` — rotation for a
directory of versioned files (e.g. dated SIF builds) that never removes a
file any symlink in the directory currently references; and ``sweep`` —
tars an inode-hog directory in place (compute-node-only, explicit
per-directory confirm required). Every mutating command defaults to a
dry-run.

Public names — including ``__version__`` — are exposed via PEP 562
``__getattr__`` (lazy, on first access) so ``import scitex_storage`` itself
stays cheap — every CLI invocation and every shell tab-completion pays this
cost (see the python-api skill's "PEP 562 module __getattr__" section).
``__version__`` in particular defers even the ``import importlib.metadata``
statement itself, not just the ``version()`` call — that stdlib import
alone measured ~280ms in this environment (dist-metadata scanning cost,
not this package's own code), so doing it eagerly at module top-level
would defeat the whole point of the lazy design.
"""

from __future__ import annotations

# Public-name -> source-submodule map. ONE row per public symbol.
_LAZY_ATTRS: dict[str, str] = {
    "ChildUsage": "_scan",
    "RootScan": "_scan",
    "scan": "_scan",
    "scan_roots": "_scan",
    "ApplyResult": "_images",
    "PruneCandidate": "_images",
    "PrunePlan": "_images",
    "SkippedInUse": "_images",
    "apply_prune": "_images",
    "plan_prune": "_images",
    "SweepCandidate": "_sweep",
    "SweepPlan": "_sweep",
    "SweepResult": "_sweep",
    "SweptCandidate": "_sweep",
    "SweptEntry": "_sweep",
    "apply_sweep": "_sweep",
    "plan_sweep": "_sweep",
    "sweep_status": "_sweep",
}


def __getattr__(name: str):
    """PEP 562 lazy-loader: import on first access, cache, return."""
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _get_version

        try:
            value = _get_version("scitex-storage")
        except PackageNotFoundError:  # source checkout without an installed dist
            value = "0.0.0+local"  # PEP 440 local version segment
        globals()["__version__"] = value
        return value

    mod_name = _LAZY_ATTRS.get(name)
    if mod_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    attr = getattr(import_module(f".{mod_name}", __name__), name)
    globals()[name] = attr  # cache; subsequent access skips this branch
    return attr


def __dir__() -> list[str]:
    return sorted(set(_LAZY_ATTRS) | set(globals()))


# Literal (not computed) so PA-101's static AST check can see it. Keep in
# sync with the _LAZY_ATTRS keys above.
__all__ = [
    "ChildUsage",
    "RootScan",
    "scan",
    "scan_roots",
    "ApplyResult",
    "PruneCandidate",
    "PrunePlan",
    "SkippedInUse",
    "apply_prune",
    "plan_prune",
    "SweepCandidate",
    "SweepPlan",
    "SweepResult",
    "SweptCandidate",
    "SweptEntry",
    "apply_sweep",
    "plan_sweep",
    "sweep_status",
    "__version__",
]

# EOF

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage: research-data storage triage.

MVP scope (see README for the full roadmap): a read-only directory-tree
``scan`` that inventories the biggest space and inode (file-count)
consumers per top-level child of a root. Nothing in this release moves,
deletes, or otherwise mutates anything on disk — it only stats.

Public names are exposed via PEP 562 ``__getattr__`` (lazy, on first
access) so ``import scitex_storage`` itself stays cheap — every CLI
invocation and every shell tab-completion pays this cost (see the
python-api skill's "PEP 562 module __getattr__" section).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version

try:
    __version__ = _get_version("scitex-storage")
except PackageNotFoundError:  # source checkout without an installed dist
    __version__ = "0.0.0+local"  # PEP 440 local version segment

# Public-name -> source-submodule map. ONE row per public symbol.
_LAZY_ATTRS: dict[str, str] = {
    "ChildUsage": "_scan",
    "RootScan": "_scan",
    "scan": "_scan",
    "scan_roots": "_scan",
}


def __getattr__(name: str):
    """PEP 562 lazy-loader: import on first access, cache, return."""
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
    "__version__",
]

# EOF

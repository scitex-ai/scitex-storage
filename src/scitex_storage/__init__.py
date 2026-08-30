#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage: research-data storage triage.

Current scope (see README for the full roadmap): a read-only, stat-only
directory-tree ``scan`` (delegates its walk to ``fd``) that inventories
the biggest space and inode (file-count) consumers per top-level child of
a root; ``find_duplicates`` — an explicitly opt-in verb that DOES read
file contents (delegated to ``fclones``) to report exact-duplicate
groups; ``images prune`` — rotation for a directory of versioned files
(e.g. dated SIF builds) that never removes a file any symlink in the
directory currently references; ``sweep`` — tars an inode-hog directory
in place (compute-node-only, explicit per-directory confirm required);
and ``archive``/``restore`` — move-not-delete tiering to nas/nas2 over
ssh, verified before the local copy is removed, with a manifest
``restore`` reads back. Every mutating command defaults to a dry-run.

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
    "ChildUsage": "_measure._scan",
    "MissingSystemDependencyError": "_measure._scan",
    "RootScan": "_measure._scan",
    "scan": "_measure._scan",
    "scan_roots": "_measure._scan",
    "find_duplicates": "_measure._duplicates",
    "ApplyResult": "_images",
    "PruneCandidate": "_images",
    "PrunePlan": "_images",
    "SkippedInUse": "_images",
    "apply_prune": "_images",
    "plan_prune": "_images",
    "SweepCandidate": "_transfer._sweep",
    "SweepPlan": "_transfer._sweep",
    "SweepResult": "_transfer._sweep",
    "SweptCandidate": "_transfer._sweep",
    "SweptEntry": "_transfer._sweep",
    "apply_sweep": "_transfer._sweep",
    "plan_sweep": "_transfer._sweep",
    "sweep_status": "_transfer._sweep",
    "ArchiveManifest": "_transfer._archive",
    "ArchivePlan": "_transfer._archive",
    "RestorePlan": "_transfer._archive",
    "apply_archive": "_transfer._archive",
    "apply_restore": "_transfer._archive",
    "plan_archive": "_transfer._archive",
    "plan_restore": "_transfer._archive",
    "Classification": "_document_pipeline",
    "DocumentSorterConfig": "_document_pipeline",
    "DocumentOutcome": "_document_pipeline",
    "RawDocument": "_document_pipeline",
    "RunSummary": "_document_pipeline",
    "ScanSnapFolderSource": "_document_pipeline",
    "Source": "_document_pipeline",
    "classify": "_document_pipeline",
    "detect_keep_original": "_document_pipeline",
    "extract_date": "_document_pipeline",
    "extract_text": "_document_pipeline",
    "load_config": "_document_pipeline",
    "process_document": "_document_pipeline",
    "process_inbox": "_document_pipeline",
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
    "MissingSystemDependencyError",
    "RootScan",
    "scan",
    "scan_roots",
    "find_duplicates",
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
    "ArchiveManifest",
    "ArchivePlan",
    "RestorePlan",
    "apply_archive",
    "apply_restore",
    "plan_archive",
    "plan_restore",
    "Classification",
    "DocumentSorterConfig",
    "DocumentOutcome",
    "RawDocument",
    "RunSummary",
    "ScanSnapFolderSource",
    "Source",
    "classify",
    "detect_keep_original",
    "extract_date",
    "extract_text",
    "load_config",
    "process_document",
    "process_inbox",
    "__version__",
]

# EOF

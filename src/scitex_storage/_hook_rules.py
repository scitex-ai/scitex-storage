"""scitex-storage's OWN agent guardrails, declared as a leaf of the federation.

Registered under the ``scitex_dev.hooks`` entry-point group in pyproject.toml
exactly like scitex-dev's own provider (``scitex_dev._hook_rules:provide``).
scitex-dev holds no privileged position in the corpus it aggregates, and
neither does this module: it declares storage's scope, nothing else.

SCOPE. These are the storage-DATA guardrails -- dry-run default, verified
delete, declared system dependencies, logging discipline. They are
scitex-storage's domain because scitex-storage is the package that moves and
deletes research data. Guardrails about the dev workflow (git/test/edit
discipline) belong to scitex-dev; guardrails about containers and agent
lifecycle belong to scitex-agent-container. Each declares its own.

PROVENANCE. Every rule below was read off the implementation and the
incident record, not reconstructed from documentation:

1. ``archive`` deleted a source on a COUNT+SIZE tally gate that CI proved is
   fooled by same-length wrong-content (2026-08-11). The delete gate is now
   copy -> independent read-back -> manifest -> delete, and
   ``ArchiveNotVerifiedError`` keeps the source on any doubt.
2. ``sweep`` wrote its tar BESIDE the source it was meant to relieve --
   filling the disk it was sent to save -- with no preflight and no
   destination (2026-07-22). Destructive verbs run dry-run first; ``--yes``
   is the only mutation gate.
3. ``archive``/``restore`` could not run in the container this package ships
   to: ``rsync`` was never declared, only ``scitex_ssh`` (the adapter) was
   (2026-07-17). Every verb now declares its system binary through the
   ``scitex_dev.system_deps`` federation and raises
   ``MissingSystemDependencyError`` with install instructions instead of
   failing mid-run.
4. PS-220 mandates scitex-logging for all human-facing output: no builtin
   ``print`` in ``src/``.

``implemented_in`` names the code that enforces the rule today. Where the
binding is in-process only, ``script`` is None and the rule is enforced by
the named implementation, not by a shell shim -- declared here so the scope
is enumerable and auditable, exactly as the federation intends.
"""

from __future__ import annotations

try:
    from scitex_dev.hooks import HookRule
except ImportError:  # pragma: no cover - provider loads under scitex-dev
    HookRule = None  # type: ignore[assignment]


def _rule(**kw):  # noqa: ANN001, ANN202 - mirrors scitex-dev's own helper shape
    kw.setdefault("event", "pre-tool-use")
    return HookRule(**kw)


def provide() -> list:
    """Return scitex-storage's declared guardrails (leaf scope)."""
    if HookRule is None:  # pragma: no cover - provider loads under scitex-dev
        return []
    return [
        _rule(
            id="storage.dry-run-default",
            rule="Destructive storage verbs run dry-run first; `--yes` is "
            "the only mutation gate.",
            reason="sweep (2026-07-22) wrote its tar beside the source it "
            "was meant to relieve, with no preflight and no destination. "
            "A dry-run that shows the plan before anything moves is what "
            "would have caught it.",
            severity="deny",
            matches=("Bash",),
            implemented_in="scitex_storage._cli (`--yes` gate on archive / "
            "sweep / restore / reclaim verbs)",
            doctrine="src/scitex_storage/_skills/scitex-storage/"
            "05_safety-contract.md §1",
        ),
        _rule(
            id="storage.verify-before-source-delete",
            rule="Never delete source data except through the verified "
            "delete gate: copy -> independent read-back -> manifest -> "
            "delete, and any doubt keeps the source.",
            reason="archive's COUNT+SIZE tally gate was proved by CI to be "
            "fooled by same-length wrong-content (2026-08-11): the original "
            "is gone and the copy is corrupt. `ArchiveNotVerifiedError` "
            "now leaves the source intact, and the opt-in content gate "
            "replaces the tally verdict with per-file sha256.",
            severity="deny",
            matches=("Bash",),
            implemented_in="scitex_storage._transfer._archive:apply_archive "
            "+ ArchiveNotVerifiedError",
            doctrine="src/scitex_storage/_skills/scitex-storage/"
            "05_safety-contract.md §2-4",
        ),
        _rule(
            id="storage.system-deps-declared",
            rule="A verb that needs `fd`, `fclones` or `rsync` declares it "
            "through the `scitex_dev.system_deps` federation and raises "
            "`MissingSystemDependencyError` with install instructions "
            "instead of failing mid-run.",
            reason="archive/restore could not run in the shipped container: "
            "rsync was never declared, only the scitex_ssh adapter that "
            "wraps it was (2026-07-17). An import gate cannot catch that "
            "class of gap -- `import scitex_ssh` resolves fine right up to "
            "the missing binary underneath.",
            severity="warn",
            matches=("Bash",),
            implemented_in="scitex_storage._system_deps:provide",
            doctrine="src/scitex_storage/_skills/scitex-storage/"
            "05_safety-contract.md",
        ),
        _rule(
            id="storage.manifest-sidecar-on-destination",
            rule="Archived trees carry a manifest sidecar on the "
            "destination, and cold scrub re-verifies trees against "
            "manifests instead of trusting local state.",
            reason="Local loss must not orphan an archive: without a "
            "destination-side manifest there is nothing to verify against "
            "once the source is gone (sidecar 0.6.5, scrub 0.6.8).",
            severity="warn",
            matches=("Bash",),
            implemented_in="scitex_storage._transfer._archive + "
            "scitex_storage._transfer._scrub",
            doctrine="src/scitex_storage/_skills/scitex-storage/"
            "05_safety-contract.md §5",
        ),
        _rule(
            id="storage.no-builtin-print",
            rule="Human-facing output goes through scitex-logging; no "
            "builtin `print` in `src/`.",
            reason="PS-220 mandates scitex-logging for all human-facing "
            "output so fleet log aggregation sees one schema instead of "
            "ad-hoc stdout.",
            severity="warn",
            matches=("Bash",),
            implemented_in="PS-220 audit (`audit_all_for_package`) + "
            "scitex_storage._server / scitex_storage._system_deps usage",
        ),
    ]

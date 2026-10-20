---
name: scitex-storage
description: Work on the scitex-storage package (safe disk-tier data handling). Use for its engine layout, safety contract, CLI verbs, GUI shared-element rules, and test conventions. User-invocable SkILL for agents and humans operating research-data storage.
user-invocable: true
---

# scitex-storage

Research-data storage triage: read-only stat-only scan plus move-not-delete
tiering (`archive`/`restore`) between disk tiers, with manifests and
verification before any source is removed.

## Engine layout (`src/scitex_storage/`)

- `_transfer/` — relocates bytes or proves a relocation landed. Every verb
  splits into `plan_*` (pure, safe to call anywhere) / `apply_*` (mutates).
  No re-exports: import the module you depend on. Test seam: pass
  `runner=` to avoid real SSH; tests sandbox `$HOME` (manifest dir resolves
  fresh every call).
- `_measure/` — strictly read-only verdicts about trees (scan, duplicates,
  inodes, survey). Never mutates; this is the line separating it from
  `_transfer/`.
- `_observe/` (`df`, hosts), `_render/` (sunburst/bubbles/fleet table),
  `_alarm/`, `_images/`, `_document_pipeline/` — feature engines.
- `_broker.py` — typed (pydantic) storage contract: `plan_storage` is a pure
  plan validator with idempotency keys and audit context. `NasExecutor` /
  `NodeProjector` are protocols (stage0): no in-repo implementations yet, no
  adapter into `_transfer` engines. Do not present the broker as an
  execution engine.
- `_cli/` — verbs behind a lazy registry (`_lazy.py`): help/completion must
  never fail because an unrelated verb's dependency is skewed. Domain
  verdict exit codes start at 10; 1/2 stay generic failure/usage error.
- `_django/` — GUI leaf. Views call backend `plan_*` functions only
  ("No web apply"); the Move tab is a read-only tier overview + archive
  planner. `resolve_project_scope` is fail-closed (`None` = explicit
  no-project state, never silent user-space fallback).

## Safety contract (non-negotiable)

1. Dry-run by default; `--yes` is the only mutation gate.
2. Copy → independent read-back → manifest → delete source ONLY on a
   positive verdict. "Could not check" blocks the delete as firmly as
   "check failed" (`ArchiveNotVerifiedError` leaves the source intact).
3. The delete gate today is tally (entry count + bytes); per-file remote
   sha256 read-back is a known gap — do not claim content verification
   the code does not perform.
4. `checksum` defaults on (rsync `--checksum`); destination free-space
   preflight before a byte moves.
5. Manifests live at `~/.scitex/scitex-storage/runtime/archive-manifests`
   (per-machine local); refusals are written into the manifest (auditable).
6. Destinations are host aliases (`scitex-nas-01/02/03`; retired `nas*`
   aliases still accepted with rewrite notice). There is no hot/warm/cold
   tier type yet — do not invent one in passing.

## GUI shared-element rules (scitex-sdk)

- Templates extend `scitex_sdk/app/app_shell.html`, which is only a block
  shim — the leaf links every SDK stylesheet it needs itself via
  `{% static 'scitex_sdk/ui/css/...' %}` (same pattern as `_app_header.html`
  linking `app-header.css`). Never assume the hub base page loads SDK CSS.
- Adopted shared primitives: `.stx-layout-fill` (`utils/layout.css`,
  needs scitex-sdk>=0.3.8) on organize roots instead of any leaf
  width/max-width rule (the SDK's no-`!important` contract forbids
  competing rules on the same element); SDK `AppHeader`; header project
  picker (rendered only when the host provides the tag library).
- Tabs (`?tab=` server-rendered links) stay leaf-owned until the SDK
  content-tabs standard is consumable AND adoption guidance exists —
  converging without guidance is guessing.

## Test conventions

- TDD: failing test first, watched fail for the right reason, minimal code,
  full suite after. AAA markers (`# Arrange/# Act/# Assert`).
- GUI tests use the real Django test client/request factory against real
  temp dirs (`test_views.py` helpers); template/static assertions read the
  real rendered body and repo files, never constants.
- When the venv's editable install points at another worktree, run with
  `PYTHONPATH=<worktree>/src` so tests import the tree under test.

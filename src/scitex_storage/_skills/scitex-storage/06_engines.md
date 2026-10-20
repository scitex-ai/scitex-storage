---
description: |
  [TOPIC] scitex-storage engine layout
  [DETAILS] _transfer/_measure split, broker stage0 status, CLI registry
  and GUI thin-shell boundaries.
tags: [scitex-storage-engines]
---

# Engine layout (`src/scitex_storage/`)

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

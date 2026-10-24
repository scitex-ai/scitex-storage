---
description: |
  [TOPIC] scitex-storage Python API
  [DETAILS] plan/apply split, typed broker contract, import paths.
tags: [scitex-storage-python-api]
---

# Python API

```python
import scitex_storage as st

plan = st.plan_archive(source, "scitex-nas-01")   # pure: measure + resolve
manifest = st.apply_archive(plan, verify_content_too=True)  # mutates
retired = st.apply_archive(plan, retire_source=True)  # mutates, keeps restorable
st.sweep_restorable()  # dry-run default: report expired staging
```

- Every transfer verb splits `plan_*` / `apply_*` (see `02` engines leaf
  naming in `06_engines.md`). Importers name the module they use
  (`from scitex_storage._transfer._archive import …`); the package
  re-exports nothing per-module to keep dependencies legible.
- `_broker.plan_storage(request, policy)` is a pure typed
  (pydantic) plan validator with idempotency keys — stage0, not an
  execution engine.
- Test seam: pass `runner=` to transfer calls to avoid real SSH; sandbox
  `$HOME` (manifest dir resolves fresh every call).
- `retire_source=True` stages the verified source aside instead of
  deleting it (`manifest.staged_path`); `sweep_restorable()` deletes
  expired staging (see the safety contract).

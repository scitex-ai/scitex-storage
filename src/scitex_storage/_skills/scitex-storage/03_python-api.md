---
description: |
  [TOPIC] scitex-storage Python API
  [DETAILS] Lazy public exports, read-only planning, reviewed archive/backup choices and typed broker scope.
tags: [scitex-storage-python-api]
---

# Python API

The package exposes public names lazily on first access. For example,
`st.plan_archive`, `st.apply_archive` and `st.sweep_restorable` resolve to
functions in their owning transfer modules. Explicit module imports remain
available when a caller needs a specific implementation.

## Plan without transferring data

Use an existing source directory and a supported destination alias. Planning
requires `fd` or `fdfind`: it measures the directory's metadata and constructs
the target and manifest paths, without network access or writing a manifest.

```python
from pathlib import Path
import scitex_storage as st

source = Path("/path/to/completed-experiment")
plan = st.plan_archive(source, "scitex-nas-01")
```

## Apply a reviewed plan

Python `apply_*` calls perform mutations immediately. The helpers below define
alternative operations; defining them does not transfer or remove anything.
Choose one only after reviewing the plan and authorizing the operation.

```python
def archive_reviewed_plan(plan):
    return st.apply_archive(plan)


def backup_reviewed_plan(plan):
    return st.apply_archive(
        plan, remove_source=False, verify_content_too=True
    )
```

- Archive defaults are `remove_source=True`, `retire_source=True` and
  `retention_days=7.0`. A verified source is moved to restorable staging;
  it is not deleted immediately.
- Every relocating move requires independent source/destination SHA256
  verification and an unchanged source before retirement. Setting
  `verify_content_too=False` cannot disable that move gate.
- Backup keeps the source with `remove_source=False`; the example also
  requests independent content verification for this copy-only operation.
- `runner=` is the transfer test seam. Tests use owned temporary trees and
  a simulated transport, rather than contacting configured SSH hosts.

## Inspect recovery staging

```python
report = st.sweep_restorable()  # dry_run=True: preview only
```

Applying cleanup requires an explicit `dry_run=False`. Age alone does not
permit deletion: committed records, markers, content checks, bounded budgets
and any required destination verification still apply. Legacy records do not
become deletion permission. See `05_safety-contract.md`.

## Typed broker

`st.plan_storage(request, policy)` validates a typed, idempotency-aware
storage request. It is the stage0 broker contract, not a transfer execution
engine. The transfer operations and their prerequisites are described in
`06_engines.md`.

## Source and behavior checks

Public exports are declared in `scitex_storage/__init__.py`. Archive defaults
and verification gates belong to `_transfer/_archive.py`; the corresponding
`tests/scitex_storage/_transfer/test__archive.py` exercises planning, source
retirement and copy-only preservation with simulated transport. Recovery
checks belong to `_transfer/_retention.py` and its `test__retention.py` tests.
The typed planning contract is implemented in `_broker.py`.

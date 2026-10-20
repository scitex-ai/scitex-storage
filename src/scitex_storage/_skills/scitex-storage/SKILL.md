---
name: scitex-storage
description: |
  [WHAT] Safe research-data storage handling: read-only scans plus
  move-not-delete tiering with manifests and verification before delete.
  [WHEN] Working on scitex-storage, moving data between disk tiers, or
  building on its GUI/CLI.
  [HOW] Plan/apply split; dry-run default + --yes gate; adopt SDK shared
  elements for GUI; AAA+TDD tests mirroring src.
tags: [scitex-storage]
user-invocable: true
---

# scitex-storage

Safe handling of research data between disk tiers: read-only measurement
plus `archive`/`restore` moves that verify before deleting anything.

## Sub-skills

- [01_installation.md](01_installation.md) — extras, SDK floor, system binaries
- [02_quick-start.md](02_quick-start.md) — 30-second tour
- [03_python-api.md](03_python-api.md) — plan/apply split, broker contract
- [04_cli-reference.md](04_cli-reference.md) — verbs, dry-run, exit codes
- [05_safety-contract.md](05_safety-contract.md) — dry-run default,
  tally vs content gates, manifest rules
- [06_engines.md](06_engines.md) — package layout and engine boundaries
- [07_gui-shared-elements.md](07_gui-shared-elements.md) — SDK adoption
  rules for the GUI leaf
- [08_test-conventions.md](08_test-conventions.md) — TDD, AAA, mirror
  and single-assert rules
- [20_env-vars.md](20_env-vars.md) — every supported env var

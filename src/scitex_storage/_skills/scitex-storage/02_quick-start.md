---
description: |
  [TOPIC] scitex-storage 30-second tour
  [DETAILS] Scan a tree, plan a tier move, verify content, restore it.
tags: [scitex-storage-quick-start]
---

# Quick start

```bash
scitex-storage survey ~/proj/my-exp        # read-only: what lives here
scitex-storage archive ~/proj/old-exp --to scitex-nas-01
        # dry-run by default: prints the plan, moves nothing
scitex-storage archive ~/proj/old-exp --to scitex-nas-01 --yes
        # syncs, reads the destination back, writes the manifest,
        # removes the source ONLY on a positive verdict
scitex-storage archive ~/proj/paper --to scitex-nas-02 --yes --verify-content
        # same, but the delete needs an independent per-file re-hash
scitex-storage verify-content SRC DST      # is it safe to delete SRC yet?
scitex-storage restore ~/proj/old-exp     # bring it back from the manifest
scitex-storage skills list                 # bundled agent skill docs
```

GUI: `scitex-storage gui open` (standalone) or `/apps/storage/` on the hub
(Usage / Move / Duplicates tabs; Move plans but never applies).

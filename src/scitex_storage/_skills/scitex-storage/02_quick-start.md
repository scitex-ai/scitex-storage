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
scitex-storage archive ~/proj/old-exp --to scitex-nas-01 --yes --retire-source
        # verified copy, then the source moves to restorable staging
        # (swept after retention) instead of being deleted
scitex-storage sweep-restorable --yes  # delete expired staged originals
scitex-storage verify-content SRC DST      # is it safe to delete SRC yet?
scitex-storage restore ~/proj/old-exp     # bring it back from the manifest
scitex-storage skills list                 # bundled agent skill docs
```

Move vs backup: `archive` relocates (source removed or staged restorable
after verification — freeing the fast disk); `backup` copies verified and
keeps the source. Same verification, opposite fate for the original.

GUI: `scitex-storage gui open` (standalone) or `/apps/storage/` on the hub
(Usage / Move / Duplicates / Backup tabs; the Move tab applies with an
explicit confirm and edit-rights check, Backup plans and lists recorded
manifests but never applies).

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
        # retains the source in seven-day recovery staging after SHA verification
scitex-storage archive ~/proj/paper --to scitex-nas-02 --yes --verify-content
        # independent SHA verification is mandatory for every relocating move
scitex-storage archive ~/proj/old-exp --to scitex-nas-01 --yes --retire-source
        # verified copy, then the source moves to restorable staging
        # (swept after retention) instead of being deleted
scitex-storage sweep-restorable --yes  # delete expired staged originals
scitex-storage verify-content SRC DST      # is it safe to delete SRC yet?
scitex-storage restore ~/proj/old-exp     # bring it back from the manifest
scitex-storage skills list                 # bundled agent skill docs
```

Move vs backup: `archive` relocates with a verified, restorable original;
`backup` copies verified and
keeps the source. Same verification, opposite fate for the original.

GUI: `scitex-storage gui open` (standalone) or `/apps/storage/` on the hub
(Usage / Move / Duplicates tabs; the Move tab applies with an
explicit confirm and edit-rights check, with per-user transaction manifests
and mandatory content verification).

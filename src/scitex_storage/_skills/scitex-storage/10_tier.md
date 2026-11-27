---
description: |
  [TOPIC] scitex-storage tier: cold units to a colder host, with pointers
  [DETAILS] Unit selection, per-unit archive gate, pointer format, run log,
  tiered restore, single-file archives and the remote sweep verifier.
tags: [scitex-storage-tier]
---

# Tier

`tier ROOT --to HOST` keeps a hot volume below a fill level. Python API:
`plan_tier` / `apply_tier` / `restore_tiered` (`_transfer/_tier.py`,
selection in `_transfer/_tier_plan.py`).

## Selection (read-only)

- A unit is a regular file or the largest directory subtree that can move
  whole. Every file's atime AND mtime must be older than the cutoff;
  directory atimes are ignored (listing updates them). A `noatime` mount
  adds a warning.
- A subtree holding an excluded entry, another filesystem, `.stx-restorable`,
  a pointer, a special file or an unreadable entry is descended instead of
  moved. No symlink is followed and no filesystem is crossed.
- Hard-linked files, units with a visible holder (cwd, root, exe, fd, map)
  or a mount at/under them, and paths with a pointer are skipped with the
  reason in `plan.skipped`. Processes this user cannot inspect are counted
  in `uninspectable_processes` and warned about.
- Order: coldest first, then largest. `target_used_percent` stops once
  projected usage (used minus selected bytes) reaches it; `max_bytes` and
  `max_items` cap a run. `disk_usage`, `proc` and `now` are test seams.

## Apply

- Each unit is re-checked right before it moves (still there, same kind,
  same filesystem, still cold, still movable whole, no holder, no pointer).
- `apply_archive(remove_source=True, retire_source=True)` with no excludes,
  so excluded data is never staged with a moved parent.
- Pointer `<path>.stx-tiered.json`, schema `scitex-storage/tier-pointer/v1`,
  written only after the archive returned: `original_path`, `kind`,
  `destination`, `destination_tier`, `remote_path`, `size_bytes`,
  `file_count`, `content_digest`, `verification_method`, `verified`,
  `manifest_path`, `staged_path`, `tiered_at` (UTC), `tier_run_id`, `tool`,
  `tool_version`, `restore_hint`.
- Run log `runtime/tier-logs/<run_id>.json`, schema
  `scitex-storage/tier-run/v1`: written before the first mutation, then per
  unit (`archiving` → `tiered` / `skipped`+reason / `failed`+error). A
  failed unit keeps its source and has no pointer. CLI exit 21 = any failed.

## Restore

`restore PATH --yes` on a path with a pointer calls `restore_tiered`. It
refuses an occupied original path and a pointer/manifest digest
disagreement. It pulls with `apply_restore(verify_content=True)`, which
raises `RestoreNotVerifiedError` before any `--delete-remote` when the
restored digest differs, and removes the pointer only after a match.

## Single-file archives

`plan_archive(..., allow_file=True)` archives one regular file as the only
entry of the remote directory `remote_path`, so tally, digest and scrub
still operate on a directory. `source_kind` is `file` and `content_digest`
is the file's sha256 (the retention snapshot convention);
`remote_tree_digest(manifest)` gives the tree digest a remote re-hash must
reproduce. The `archive` CLI stays directory-only.

## Freeing the space

The original stays in `.stx-restorable/` on the same filesystem.
`sweep-restorable --verify-remote --yes` supplies `remote_target_verifier`:
it re-hashes the remote copy over ssh with the remaining `--max-seconds` as
timeout and returns True only for a full read whose digest matches before
the deadline. Anything else defers the original. Timer units:
`examples/systemd/scitex-storage-tier.{service,timer}` + `tier.env.example`
(sweep first, then tier; `Nice=10`, idle I/O).

# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- `scitex-storage archive SOURCE --to nas|nas2 [--remote-path PATH] [--exclude PATTERN ...] [--checksum/--no-checksum] [--yes|-y] [--dry-run]`
  — move-not-delete tiering to nas/nas2 over ssh, built on scitex-ssh's
  `sync_dir` (rsync-over-ssh). Copy-verify-then-remove: pushes SOURCE,
  verifies the sync (checksummed by default), writes a manifest under
  `~/.scitex/scitex-storage/runtime/archive-manifests/`, and ONLY THEN
  removes the local copy. A failed sync leaves SOURCE completely untouched
  and no manifest is written. Defaults to a dry-run; `--yes`/`-y` is
  required to actually sync + remove (canonical mutating-verb flags per
  the ecosystem convention, unlike `images prune`/`sweep`'s `--apply` —
  `archive`/`restore` are audit-cli's hardcoded mutating-verb list, those
  two aren't). Creates the remote parent directory (`mkdir -p` via
  `exec_remote`, not `rsync --mkpath` — the latter needs rsync 3.2.3+,
  and a real destination still runs 3.0.7) before the sync, since a
  destination that's never held archived data has no
  `~/scitex-storage-archive/...` tree yet — the common first-use case,
  found by scitex-ssh smoke-testing a real archive against real nas2.
- `scitex-storage restore SOURCE [--delete-remote] [--yes|-y] [--dry-run]`
  — reads the manifest `archive` wrote for SOURCE and pulls the data back.
  The remote copy is kept by default; `--delete-remote` removes it after a
  verified restore. Defaults to a dry-run.
- `scitex-storage sweep DIRECTORY --threshold-files N [--min-age-hours H] [--apply --confirm NAME ...]`
  — tar an inode-hog directory in place (many small files -> one tar, one
  inode). Candidates are immediate children of DIRECTORY at or above
  `--threshold-files`, excluding anything whose newest file is younger than
  `--min-age-hours` (default 24h — protects a directory still in active
  use). `--apply` requires an explicit `--confirm NAME` per directory,
  never a blanket "sweep everything the plan found". Compute-node-only:
  refuses to run unless `$SLURM_JOB_ID` is set. Checks remaining SLURM
  walltime before starting each candidate and stops rather than risking a
  kill mid-tar. Defaults to a dry-run.
- `scitex-storage sweep-status DIRECTORY` — read-only listing of directories
  already swept (a sibling `<name>.tar` exists).
- `scan`'s `ChildUsage` gained a `newest_mtime` field (max file mtime in the
  subtree) — computed in the same walk as size/file-count, no extra
  traversal; used by `sweep`'s freshness exclusion and now also exposed in
  `scan --json` output.

- `scitex-storage images prune DIRECTORY [--keep N] [--pattern GLOB] [--apply]`
  — rotate a directory of versioned files (e.g. dated SIF builds), keeping
  the newest N plus every file any symlink in the directory currently
  references (those are never removed regardless of N). Before unlinking,
  also checks `/proc` for any process with the candidate open and skips it
  (loudly, never raises) — a second guard for a file that dropped out of
  the symlink target mid-swap but is still open by a running process.
  Defaults to a dry-run; `--apply` is required to actually delete.

## [0.1.0]

- Initial bootstrap: `scitex-storage scan [PATH ...]` MVP — a read-only,
  stat-only walk reporting the biggest space and inode (file-count)
  consumers per top-level child of a root (defaults to `~/.scitex` and
  `~/proj`). Never follows symlinked directories, never reads file
  contents, never mutates anything.

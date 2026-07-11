# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

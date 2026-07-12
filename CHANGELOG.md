# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- `scan`'s directory walk now delegates to `fd` instead of Python
  `os.walk`, for multi-terabyte-scale performance. `fd` is a new
  **system** (non-PyPI) runtime dependency of `scan` only — see the
  README's "System dependencies" section — declared to the
  `scitex_dev.system_deps` federation. Missing the binary raises
  `MissingSystemDependencyError` with install instructions; there is no
  silent slow-path fallback. `scan`'s public API and read-only,
  stat-only contract are unchanged.
- New `scitex-storage find-duplicates PATH [PATH ...]` — a separate,
  explicitly opt-in verb that finds exact-duplicate files via `fclones`
  (a new system dependency of this verb only). Deliberately NOT part of
  `scan`: finding exact duplicates requires reading file contents, which
  would break `scan`'s "always safe on a nearly-full disk / network
  mount" guarantee.

## [0.2.0] - 2026-07-12

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
  Every shell command built from a remote path (the `mkdir -p` above,
  `rm -rf` for `restore --delete-remote`) leaves a leading `~` unquoted —
  a naive `shlex.quote()` of the whole path turns `~` into a literal
  character (tilde-expansion only applies unquoted), silently creating a
  directory named `~` instead of resolving `$HOME` — also found by
  scitex-ssh's real-nas2 smoke test. Both `archive` (push) and `restore`
  (pull) add a trailing `/` to the copy source so rsync copies its
  *contents* directly into the destination rather than nesting the source
  one level deeper as a subdirectory — without it, a restored tree landed
  two directories deep with byte-correct but wrongly-placed data, caught
  by scitex-ssh diffing actual restored file paths, not just checksums.
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

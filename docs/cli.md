# Interfaces

Flags and examples for the `scitex-storage` CLI verbs, then the Python API.
Every mutating verb is a dry-run unless `--yes` (or `--apply` where noted)
is given.

## CLI

```bash
scitex-storage scan [PATH ...] [--top N] [--sort size|files] [--max-depth D] [--json]
scitex-storage find-duplicates PATH [PATH ...] [--max-depth D] [--json]
```

**`scan`** (stat-only, `fd`-backed — never reads file contents):

| Flag | Default | Meaning |
|---|---|---|
| `PATH ...` | `~/.scitex ~/proj` | One or more roots. Missing default roots are skipped; a missing *explicit* PATH is a hard error |
| `--top N` | 20 | How many top children to print per root |
| `--sort` | `size` | Rank children by total `size` or by inode `files` count |
| `--max-depth D` | unlimited | Cap recursion depth per child (login-node / network-path safety) |
| `--json` | off | Emit machine-readable JSON instead of the text table |

```bash
scitex-storage images prune DIRECTORY [--keep N] [--pattern GLOB] [--apply] [--json]
```

| Flag | Default | Meaning |
|---|---|---|
| `DIRECTORY` | (required) | Directory of versioned files to rotate, e.g. a SIF build dir |
| `--keep N` | 5 | Target retained count. Files any symlink in `DIRECTORY` references are kept on top of this |
| `--pattern` | `*.sif` | Glob matched against candidate filenames |
| `--apply` | off | Actually delete. Skips (never deletes) any candidate a running process still has open. Without it, prints the plan only (dry-run) |
| `--json` | off | Emit machine-readable JSON instead of the text report |

```bash
$ scitex-storage images prune ~/.scitex/agent-container/containers/sac-base
scitex-storage images prune  /home/user/.scitex/agent-container/containers/sac-base
====================================================================================
1 referenced (always kept), 4 newest kept, 2 to remove

  WOULD REMOVE:
        1.3 GB  sac-base-2026-0512-212752.sif
        1.3 GB  sac-base-2026-0513-083535.sif

  2.6 GB reclaimable
  (dry-run — pass --apply to actually delete)
```

```bash
scitex-storage sweep DIRECTORY --threshold-files N [--min-age-hours H] [--apply --confirm NAME ...] [--min-remaining-seconds S] [--json]
```

| Flag | Default | Meaning |
|---|---|---|
| `DIRECTORY` | (required) | Directory whose immediate children are sweep candidates |
| `--threshold-files N` | (required, no default) | Minimum file count to qualify as a candidate |
| `--min-age-hours` | 24 | Exclude a candidate if its newest file is younger than this |
| `--apply` | off | Actually tar + remove. Requires `--confirm` |
| `--confirm NAME` | — | Explicit per-directory consent (repeatable). Never a blanket apply |
| `--min-remaining-seconds` | 300 | Stop before starting a candidate if less SLURM walltime remains |
| `--json` | off | Emit machine-readable JSON instead of the text report |

`--apply` **refuses to run outside a SLURM allocation** (`$SLURM_JOB_ID` must
be set) — tarring reads file content and the cleanup is a heavy metadata
op, both barred from Spartan login nodes. Submit via `sbatch` or
`srun --overlap --jobid=<held-allocation>`.

```bash
$ scitex-storage sweep /data/gpfs/projects/punim0264/runs --threshold-files 5000
scitex-storage sweep  /data/gpfs/projects/punim0264/runs
==========================================================
threshold >= 5,000 files, min age 24h  (1 candidate(s), 0 skipped as too fresh)

  CANDIDATES (dry-run):
        18,204  old-run-42  (~18,203 inodes reclaimable)

  18,203 inodes reclaimable
  (dry-run — pass --apply --confirm NAME [--confirm NAME ...] to sweep)
```

```bash
scitex-storage sweep-status DIRECTORY [--json]
```

Read-only: lists directories already swept (a sibling `<name>.tar` exists).

```bash
scitex-storage archive SOURCE --to nas|nas2 [--remote-path PATH] [--exclude PATTERN ...] [--checksum/--no-checksum] [--yes|-y] [--dry-run] [--json]
scitex-storage restore SOURCE [--delete-remote] [--yes|-y] [--dry-run] [--json]
```

| Flag | Default | Meaning |
|---|---|---|
| `SOURCE` | (required) | Directory to archive / the original path to restore to |
| `--to` | (required, `archive` only) | `nas` or `nas2` — the `~/.ssh/config` alias to sync to |
| `--remote-path` | mirrors `SOURCE` under `~/scitex-storage-archive` | Explicit remote path override |
| `--exclude PATTERN` | — | Glob to exclude from the sync (repeatable) |
| `--checksum` / `--no-checksum` | on | Read every byte on both sides (rsync `--checksum`) before removing the local copy |
| `--delete-remote` | off | `restore` only: remove the remote copy after a verified pull |
| `--yes`, `-y` | off | Actually sync/pull + mutate. Canonical mutating-verb flag (`archive`/`restore` are in the ecosystem's hardcoded mutating-verb list, unlike `images prune`/`sweep`'s `--apply`) |
| `--dry-run` | — | Explicit spelling of the default (no side effects either way) |
| `--json` | off | Emit machine-readable JSON instead of the text report |

Applied archive, backup, and restore attempts write a private JSON record
under `~/.scitex/scitex-storage/runtime/transfer-logs/`. A record identifies
the failed stage and rsync exit code when available. Retries have separate
records, and dry runs leave this directory untouched.

`archive` is copy-verify-then-remove, never delete-then-copy: a failed sync
leaves `SOURCE` completely untouched and no manifest is written. The
manifest (`~/.scitex/scitex-storage/runtime/archive-manifests/`) is what
`restore` reads back — it never needs `SOURCE` to still exist.

```bash
scitex-storage tier ROOT --to HOST [--remote-root PATH] [--cold-after-days N] [--min-size SIZE] [--exclude PATTERN ...] [--target-used-percent P] [--max-bytes SIZE] [--max-items N] [--json] [--yes]
scitex-storage sweep-restorable --verify-remote [--max-seconds S] [--yes]
```

**`tier`** keeps a hot volume below a fill level.

| Flag | Default | Meaning |
|---|---|---|
| `ROOT` | (required) | Directory on the hot volume to tier from |
| `--to` | (required) | Colder host alias (`scitex-nas-01/02/03`) |
| `--remote-root` | `~/scitex-storage-archive` | Each unit mirrors its absolute path under it |
| `--cold-after-days` | 90 | Cold when every file's atime AND mtime is older than this |
| `--min-size` | `100M` | Smallest unit worth moving (binary units: `500M`, `1.5G`, `2TiB`) |
| `--exclude PATTERN` | — | rsync-style pattern relative to `ROOT` that never moves (repeatable) |
| `--target-used-percent` | — | Stop selecting once ROOT's filesystem is projected at or below this |
| `--max-bytes`, `--max-items` | — | Cap one run |
| `--yes`, `-y` | off | Move the units. Without it, print the plan only |

A unit is a regular file or the largest directory subtree that can move
whole. Coldest go first, then largest. The walk never crosses filesystems
or follows symlinks. It skips `.stx-restorable`, existing pointers,
`--exclude` matches, hard-linked files and anything a visible process holds
open or has mounted. A subtree that holds any of these is descended rather
than moved whole.

`--yes` re-checks each unit and moves it with `archive`. That step copies
it, verifies SHA256 on both sides, writes a manifest and stages the original
for seven days. Only then does tier write the pointer `<path>.stx-tiered.json`
(schema `scitex-storage/tier-pointer/v1`). The pointer records the original
path, destination, remote path, size, file count, content digest, manifest
and staged paths, `tiered_at` and tool version. `restore <path> --yes`
brings a tiered unit back. It refuses an occupied path, verifies the digest,
and then removes the pointer. Each run writes
`runtime/tier-logs/<run_id>.json` before the first move and updates it after
every unit. Exit code 21 means a unit failed; its source is intact and it
has no pointer.

The space returns when `sweep-restorable --verify-remote --yes` deletes
expired staging whose remote copy still re-hashes to the recorded digest.
`examples/systemd/scitex-storage-tier.{service,timer}` run both steps daily,
configured by `tier.env.example`.

**`find-duplicates`** (`fclones`-backed — DOES read file contents to hash them; a separate, explicitly opt-in verb, never run implicitly by `scan`):

| Flag | Default | Meaning |
|---|---|---|
| `PATH ...` | *(required)* | One or more roots to search for exact duplicates |
| `--max-depth D` | unlimited | Cap recursion depth (login-node / network-path safety) |
| `--json` | off | Emit machine-readable JSON instead of the text report |

Other top-level commands (ecosystem-standard, per every scitex-* CLI):
`list-python-apis` (introspect the Python API), `mcp list-tools`
(no MCP server shipped yet — reports zero tools), `--help-recursive`.

## Python API

```python
import scitex_storage as ss

result = ss.scan("~/.scitex")           # read-only, stat-only walk
result.total_size                        # bytes across all children
result.total_files                       # inodes across all children
result.by_size()[0]                      # biggest child (ChildUsage)
result.by_file_count()[0]                # child with the most inodes

for r in ss.scan_roots(["~/.scitex", "~/proj"]):
    print(r.root, r.total_size, r.total_files)

plan = ss.plan_prune("~/.scitex/agent-container/containers/sac-base", keep=5)
plan.remove                              # candidates that would be deleted
plan.reclaimable_bytes                   # bytes those candidates hold

result = ss.apply_prune(plan)            # never touches referenced or open files
result.removed                           # candidates actually unlinked
result.skipped_in_use                    # candidates skipped: [(candidate, pids), ...]
result.reclaimed_bytes                   # bytes actually freed

sweep_plan = ss.plan_sweep("/data/gpfs/projects/punim0264/runs", threshold_files=5000)
sweep_plan.candidates                    # inode-hog children, old enough to be safe
sweep_plan.skipped_fresh                 # met the threshold but too recently touched

sweep_result = ss.apply_sweep(sweep_plan, confirm_names=["old-run-42"])  # SLURM-only
sweep_result.swept                       # [SweptCandidate(tar_path, member_count, ...), ...]
sweep_result.reclaimed_inodes

ss.sweep_status("/data/gpfs/projects/punim0264/runs")  # what's already swept

archive_plan = ss.plan_archive("~/proj/old-experiment", "nas2")
archive_plan.remote_path                 # default: mirrors the source path
manifest = ss.apply_archive(archive_plan)  # sync, verify, write manifest, remove source

restore_plan = ss.plan_restore("~/proj/old-experiment")  # reads the manifest back
ss.apply_restore(restore_plan)           # pulls it back; keeps the remote copy by default

# Separate, explicitly opt-in -- reads file contents to hash them.
groups = ss.find_duplicates(["~/proj/old-scan"])
for group in groups:
    print(len(group), "identical files:", group)
```

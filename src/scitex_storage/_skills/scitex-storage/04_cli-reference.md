---
description: |
  [TOPIC] scitex-storage CLI reference
  [DETAILS] Verb list, dry-run convention, exit codes, JSON output.
tags: [scitex-storage-cli-reference]
---

# CLI reference

| Verb | Role |
|---|---|
| `scan`, `survey` | read-only tree measurement |
| `find-duplicates`, `validate-inodes`, `find-recipe` | analysis verdicts |
| `sweep`, `sweep-status`, `reclaim` | local rotation / reclamation |
| `archive --to DST [--yes] [--verify-content] [--retire-source]` | tier move + verified delete (or restorable staging) |
| `tier ROOT --to DST [--target-used-percent P] [--yes]` | move cold files/subtrees of ROOT through `archive`, leave `<path>.stx-tiered.json` pointers; run log in `runtime/tier-logs/` (exit 21 = a unit failed) |
| `sweep-restorable [--verify-remote] [--yes]` | delete expired restorable-staged trees (dry-run lists); `--verify-remote` re-hashes remote archive copies so their originals can go |
| `backup --to DST [--yes]` | copy-verified second copy, source retained |
| `retire MANIFEST [--also DIR] [--yes]` | delete a backed-up source (and hard-linked siblings) after a fresh remote scrub + local re-hash; no staging |
| `purge PATH --confirm PATH --reason TEXT [--yes]` | audited deletion of a disposable tree with no copy (migration leftover); refuses mounts, holders, protected paths; audit in `runtime/purge-logs/` |
| `restore`, `reclaim-restore`, `relocate-restore` | bring data back |
| `relocate PLAN.yaml --root DIR [--yes]` | apply an ordered move plan: simulated, merge-safe, conflict-refusing, reversible |
| `verify-content SRC DST` | standalone delete-safety verdict |
| `scrub MANIFEST [--content]` | cold re-verify vs manifest (read-only; exit 0/16/17) |
| `images` | SIF image rotation |
| `fleet-status`, `alarm` | fleet views, free-space alarm |
| `document-sorter` | ScanSnap-style auto-sort |
| `gui`, `start-gui` | serve/open the leaf GUI |
| `skills list\|get\|install` | bundled agent skill docs |

Conventions: dry-run by default, `--yes/-y` mutates; `--json` for machine
output; domain verdict exit codes start at 10 (1/2 stay generic). Verbs
load lazily — help never fails because an unrelated verb's dep is skewed.

Alarm (`alarm`): pushes only on a level TRANSITION; exit 0 = the check ran
(even if storage is unhealthy), 30 = a push was needed and every rail
refused it (or `--check-notify` found a broken rail).

- `--path P` (repeatable) watches specific mounts instead of `~` and `/`;
  each path set keeps its own state file.
- `--warn-free-gib N` / `--crit-free-gib M` (alias `--critical-free-gib`) set
  the absolute floors (defaults 20 / 5). On volumes >= 1 TiB the floors alone
  decide; the 85%-used rule applies only to smaller volumes. Inodes keep the
  85% rule everywhere.
- `--notify cards|email|cards,email` picks rails, tried in order. `email`
  sends by direct SMTP as a scitex-notification profile (`--email-profile`
  / `$SCITEX_STORAGE_ALARM_EMAIL_PROFILE`, default `ops`) and does not touch
  the fleet database. The host configures the profile:
  `SCITEX_NOTIFICATION_PROFILE_OPS_FROM=no-reply@scitex.ai`, `_TO=dev@scitex.ai`,
  `_SMTP_HOST`, `_SMTP_PORT`; the password stays gpg-encrypted
  (`$SCITEX_STORAGE_ALARM_EMAIL_PASSWORD_GPG`, optional
  `$SCITEX_STORAGE_ALARM_EMAIL_GNUPGHOME`) and is decrypted into memory at
  send time. An unconfigured profile or an undecryptable ciphertext fails
  the rail by name; it never reroutes to a personal inbox.
  A fallback is LOUD: `detail` starts with `FALLBACK via email` and names
  the failed rail; JSON carries `rail` and `rail_errors`.
- Repeats: per filesystem, a level is mailed at most once per
  `--repeat-hours` (default 6); WARN -> CRITICAL is never held back; ok is
  mailed only as recovery from an announced WARN/CRITICAL. JSON carries
  `changes` and `suppressed`.
- `--check-notify` validates rails without sending (email: the profile
  resolves, the password decrypts, the dry run passes; SMTP login is not
  attempted; cards: import only, because probing would send a DM).
- Example (scitex-nas-03): `alarm --path /volume1 --warn-free-gib 100
  --crit-free-gib 30 --notify email`. Timer units:
  `examples/systemd/`.

Tier (`tier ROOT --to HOST`): keeps a hot volume below a fill level.

- Usage: `tier ROOT --to HOST [--remote-root PATH] [--cold-after-days N]
  [--min-size SIZE] [--exclude PAT ...] [--target-used-percent P]
  [--max-bytes SIZE] [--max-items N] [--json] [--yes]`. Sizes take binary
  units (`500M`, `1.5G`, `2TiB`). Defaults: 90 days, 100M.
- A unit is a regular file or the largest directory subtree that can move
  whole, with every file's atime AND mtime older than the cutoff. A subtree
  holding an excluded entry, another filesystem, `.stx-restorable`, a
  pointer, a special file or an unreadable entry is descended instead. No
  symlink is followed; hard-linked files and anything held open or mounted
  on are skipped with the reason in the plan.
- Order: coldest first, then largest. `--target-used-percent` stops once the
  projected usage (current minus selected bytes) reaches the target.
- `--yes` re-checks each unit, moves it with `archive` (content-verified,
  original staged for 7 days), and only then writes the pointer
  `<path>.stx-tiered.json` (schema `scitex-storage/tier-pointer/v1`:
  original path, destination, remote path, size, file count, content digest,
  manifest path, staged path, `tiered_at`, tool version, restore hint).
- `restore <path> --yes` on a tiered path refuses an occupied path, restores,
  requires the content digest to match, and then removes the pointer.
- The space comes back when `sweep-restorable --verify-remote --yes` deletes
  expired staging whose remote copy re-hashes. Timer units:
  `examples/systemd/scitex-storage-tier.{service,timer}` + `tier.env.example`.

Content versions: `versions --store ROOT inspect VERSION_ID` keeps its JSON
default; `--human` selects readable metadata (manifest verified, contents not
verified). `versions --store ROOT create-pointer VERSION_ID NEW_PATH --yes`
creates a new pointer without overwrite. Legacy `pointer` forwards the same
arguments, with a shared deprecation warning when Dev is available.

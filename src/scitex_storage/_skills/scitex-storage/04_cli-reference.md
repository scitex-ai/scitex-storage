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
| `purge PATH... [--reason R] [--min-age-days N] [--yes]` | audited delete of regenerable paths (cache verdict or stated reason; refuses held/cwd/mount/recent); audit in `runtime/purge-logs/` |
| `archive --to DST [--yes] [--verify-content] [--retire-source]` | tier move + verified delete (or restorable staging) |
| `sweep-restorable [--yes]` | delete expired restorable-staged trees (dry-run lists) |
| `backup --to DST [--yes]` | copy-verified second copy, source retained |
| `retire MANIFEST [--also DIR] [--yes]` | delete a backed-up source (and hard-linked siblings) after a fresh remote scrub + local re-hash; no staging |
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
  runs the scoped operator helper (`ywatanabe-email-notify`'s `send.sh`, or
  `--email-command` / `$SCITEX_STORAGE_ALARM_EMAIL_COMMAND`) and does not
  touch the fleet database. A fallback is LOUD: `detail` starts with
  `FALLBACK via email` and names the failed rail; JSON carries `rail` and
  `rail_errors`.
- `--check-notify` validates rails without sending (email: the helper's
  `--dry-run`; cards: import only, because probing would send a DM).
- Example (scitex-nas-03): `alarm --path /volume1 --warn-free-gib 100
  --crit-free-gib 30 --notify cards,email`. Timer units:
  `examples/systemd/`.

Content versions: `versions --store ROOT inspect VERSION_ID` keeps its JSON
default; `--human` selects readable metadata (manifest verified, contents not
verified). `versions --store ROOT create-pointer VERSION_ID NEW_PATH --yes`
creates a new pointer without overwrite. Legacy `pointer` forwards the same
arguments, with a shared deprecation warning when Dev is available.

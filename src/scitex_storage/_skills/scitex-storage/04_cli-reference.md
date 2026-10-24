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
| `sweep-restorable [--yes]` | delete expired restorable-staged trees (dry-run lists) |
| `backup --to DST [--yes]` | copy-verified second copy, source retained |
| `restore`, `reclaim-restore` | bring data back |
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

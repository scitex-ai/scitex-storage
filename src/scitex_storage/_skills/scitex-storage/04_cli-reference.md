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

`find-duplicates` reads file contents through `fclones`; `--sizes-only` is
the stat-only candidate prepass, not proof of equal content. Exact groups
exclude final symlinks and repeated regular-file inodes. Engine errors,
invalid engine JSON (including missing/malformed group or path arrays), and
files disappearing before identity inspection fail
with exit 1 and a readable stderr error, including with `--json`; stdout
does not contain a successful or partial duplicate result. A vanished-file
count with partial results is not implemented yet. Controls:
`tests/scitex_storage/_cli/test__duplicates_cmd.py` exercises the real CLI
process with owned engine-protocol fixtures; native identity controls are
in `tests/scitex_storage/_measure/test__duplicate_identity.py`.

The `--sizes-only` walk skips entries that vanish while walking, but propagates
other inspection failures (for example permission denial). Such a failure exits
nonzero with a readable stderr diagnostic and no successful or partial JSON
result. It does not count skipped entries or prove content equality. Controls:
`tests/integration/scitex_storage/_measure/test_size_scan_errors.py`.

`find-duplicates --group-by parent|top-level` adds a location breakdown of each
confirmed content group. Default output is unchanged. JSON adds `group_by` and
`location_groups`; each item retains its zero-based content `group_index`,
`relationship` (`within-location` or `across-locations`) and sorted `locations`
with canonical `directory` and `files`. Parent mode uses the immediate parent.
Top-level mode uses the most specific selected root's first child directory;
files directly under that root use the root itself. Root argument order and
directory aliases do not change ownership. Missing members and files outside
all top-level roots fail rather than being omitted. `--sizes-only --group-by`
is refused before hashing. This breakdown makes no keeper/deletion decision.
Controls: `tests/integration/scitex_storage/_measure/test_duplicate_locations.py`
and `test_duplicate_locations_cli.py` in the same directory, including native
fclones CLI checks.

Content versions: `versions --store ROOT inspect VERSION_ID` keeps its JSON
default; `--human` selects readable metadata (manifest verified, contents not
verified). `versions --store ROOT create-pointer VERSION_ID NEW_PATH --yes`
creates a new pointer without overwrite. Legacy `pointer` forwards the same
arguments, with a shared deprecation warning when Dev is available.

---
description: |
  [TOPIC] scitex-storage safety contract
  [DETAILS] Dry-run default, tally vs content delete gates, manifest and
  destination rules that every change must preserve.
tags: [scitex-storage-safety-contract]
---

# Safety contract (non-negotiable)

1. Dry-run by default; `--yes` is the only mutation gate.
2. Copy → independent read-back → manifest → retire or delete source ONLY
   on a positive verdict. "Could not check" blocks the delete as firmly as
   "check failed" (`ArchiveNotVerifiedError` leaves the source intact).
   A move stages its original for seven days by default. Copy-only mode
   (`remove_source=False`) keeps it at its original path. Explicit low-level
   `retire_source=False` remains a destructive API choice after verification.
3. Count/size read-back gates progress; independent SHA256 of both source
   and destination gates every move, even if an older caller passes
   `verify_content_too=False`. The source must still match its pre-copy
   baseline before retirement. Manifests record `verification_method=content`
   and the canonical content digest. Copy-only mode may retain the cheaper
   tally verification; that does not authorize removing the original.
   Every baseline (count/size tally and content digest) measures the SAME
   population the transport sends: `--exclude` patterns apply to it with
   rsync semantics (basename at any depth; `/`-anchored at the root). The
   remote count is BusyBox-portable (`find -print0 | tr -dc '\000' | wc -c`;
   QNAP has no GNU `find -printf`), so an unsupported primitive cannot read
   as "0 entries".
4. `checksum` defaults on (rsync `--checksum` reads every byte in flight);
   destination free-space preflight before a byte moves.
5. Manifests live at `~/.scitex/scitex-storage/runtime/archive-manifests`
   (per-machine local); refusals are written into the manifest (auditable).
6. Destinations are host aliases (`scitex-nas-01/02/03`; retired `nas*`
   aliases still accepted with rewrite notice) for the archive CLI. The GUI
   maps logical hot/warm/cool/cold tiers through `_logical_tiers.py`; never infer
   an archive host’s speed tier from its number.
7. Backup (`apply_archive(remove_source=False)`, `backup` CLI verb) copies
   and verifies exactly like archive but retains the source, recorded as
   `retained_source`. Verification is NOT relaxed: an unverified copy
   raises either way.
8. Restorable staging (operator 2026-10-24): a retired source moves aside
   atomically next to itself (`.stx-restorable/`, no extra space), recorded
   in the manifest (`staged_path`) and a central registry. Only a sweeper
   deletes it, only after its 7-day retention, only with a matching marker,
   and never outside a `.stx-restorable` directory (`sweep-restorable`,
   dry-run by default). The browser does not schedule the sweeper: expiry
   makes staging eligible, and removal still requires an explicitly applied
   sweep or a separately configured operator job.


Retention writes a durable pending intent before the no-overwrite rename;
process locks serialize registry updates. Sibling markers preserve the archived
content tree. Failed/partial transitions remain recoverable and are never
cleanup permission. `restore_staged(staged_path)` restores a registered,
checksum-matching original without overwriting a new occupant of its old path.

`sweep-restorable` defaults to a genuinely read-only preview, including when
`--dry-run` accompanies `--yes`. Applied passes require matching committed
marker, registry, and content checksum. Default limits are 32 records, 1 GiB,
4096 filesystem entries per tree, and 30 seconds of cooperative work; use
`--max-items`, `--max-bytes`, `--max-entries`, and `--max-seconds` explicitly to
change these. Larger trees remain retained. Cancellation is checked during
hash reads; blocking filesystem calls are an OS boundary. Old records without
content baselines are refused rather than silently licensed for deletion.

The safe rename currently requires Linux `renameat2(RENAME_NOREPLACE)`.
Unsupported systems fail before replacing data; there is no unsafe
exists-then-rename fallback.


`plan_move(source, destination, state_dir=..., transaction_id=...)` and
`apply_move(plan, timeout_s=120, deadline=None, cancel_event=None)` provide
local authorized-volume moves without hostnames. The host must authorize both
roots and the owner-specific state directory. Job records live at
`<state_dir>/<transaction_id>.json`; that runtime directory's parent holds
`restorable-registry.json`. No process-global HOME changes are needed.

The engine publishes a private verified copy with no-replace rename, then
stages the original. `get_move_status(id, state_dir=...)` reads the receipt;
completed replay is historical/idempotent, while other existing phases require
explicit recovery. Exceptions carry `.manifest`; `destination_published=True`
means the verified destination survives cancellation/failure. Do not report a
failed, cancelled, or still-cancelling transaction as a successful move. The
rsync process group is killed/reaped on cancellation; hashing cooperates
between reads. Nothing automatically resumes, schedules cleanup, or rewrites
project location metadata.


A MOVE-created record carries `verification_target` and
`deletion_policy=verified-copy`. Before expired source cleanup, the completed
move receipt must still match and the destination must independently re-hash to
the original baseline. A missing, altered, unreachable, or unverified copy
retains the original (`SweepReport.deferred`). Cleanup bytes/entries/time limits
apply to both copies. The host may provide a bounded remote
`target_verifier(target, *, deadline, cancelled, max_bytes, max_entries)`; only
literal True before the deadline admits deletion. No provider means defer;
never substitute an old successful receipt for current destination evidence.

Direct `stage_for_retention` without a target is manual quarantine, not proof of
a backup. Its deletion additionally requires `allow_manual_delete=True` or
`--allow-manual-quarantine`; ordinary `--yes` and TTL alone are insufficient.
Older records without this policy/copy proof remain deferred for explicit
investigation, even when the manual opt-in is supplied.

9. Relocate (`relocate PLAN.yaml --root DIR`) simulates the plan in order on a
   virtual index before any mutation. A planning CONFLICT (different content
   at a destination, or a file where a folder is needed) refuses the whole plan
   before its first move. Identical-content files dedupe.
   Junk, drops and duplicates are moved under `<archive-root>/<run-id>/` with
   their relative paths, never unlinked. Completed operations are recorded in order at
   `~/.scitex/scitex-storage/runtime/relocate-manifests/<run-id>.json`.
   During normal exception handling, a run that fails partway writes an
   incomplete manifest of its completed operations; earlier moves remain in
   place. Execution has no automatic rollback or whole-run atomicity. Inspect
   the manifest and filesystem before recovery. `relocate-restore` replays the
   recorded operations backwards and refuses to overwrite; an interrupted
   operation may need separate recovery. Manifest persistence can itself fail,
   and process crashes are outside this guarantee. Links are relative
   directory symlinks only; file and absolute links are refused.

   Evidence: `_transfer/_relocate.py::apply_relocate` and `restore_relocate`;
   planning-conflict controls in `tests/scitex_storage/_transfer/test__relocate.py`
   and real partial-run/recorded-operation recovery controls in
   `tests/integration/scitex_storage/_transfer/test_relocate_partial_run.py`.

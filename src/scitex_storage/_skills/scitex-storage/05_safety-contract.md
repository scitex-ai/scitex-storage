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
4. `checksum` defaults on (rsync `--checksum` reads every byte in flight);
   destination free-space preflight before a byte moves.
5. Manifests live at `~/.scitex/scitex-storage/runtime/archive-manifests`
   (per-machine local); refusals are written into the manifest (auditable).
6. Destinations are host aliases (`scitex-nas-01/02/03`; retired `nas*`
   aliases still accepted with rewrite notice). There is no hot/warm/cold
   tier type yet — do not invent one in passing.
7. Backup (`apply_archive(remove_source=False)`, `backup` verb/tab) copies
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

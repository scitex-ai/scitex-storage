---
description: |
  [TOPIC] scitex-storage safety contract
  [DETAILS] Dry-run default, tally vs content delete gates, manifest and
  destination rules that every change must preserve.
tags: [scitex-storage-safety-contract]
---

# Safety contract (non-negotiable)

1. Dry-run by default; `--yes` is the only mutation gate.
2. Copy → independent read-back → manifest → delete source ONLY on a
   positive verdict. "Could not check" blocks the delete as firmly as
   "check failed" (`ArchiveNotVerifiedError` leaves the source intact).
3. Two delete gates, honestly named in the manifest (`verification_method`)
   and the CLI report ("delete cleared by: …"):
   - `tally` (default): entry count + total bytes read back. Cheap;
     catches transport errors, not same-size corruption.
   - `content` (opt-in via `--verify-content`): independent per-file
     sha256 of BOTH trees (`_remote_digest` over ssh, BusyBox-safe),
     replacing the tally verdict. Costs a full re-read of both sides —
     that is why it is opt-in, and the manifest records which gate ran.
   When the content gate runs, the manifest also keeps the source's
   canonical tree digest (`content_digest`, zero marginal cost) as the
   baseline for cold scrub; tally-cleared manifests carry none, and a
   scrub reports content as unknown rather than manufacturing one.
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

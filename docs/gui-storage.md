# Storage GUI contract

The implementation lives in `_django/organize.py`, `_django/logical_move.py`,
`_django/volumes.py`, and `_logical_tiers.py`. The only organize tabs are Usage,
Move, and Duplicates. The CLI backup operation is independent of these tabs.

## Scope and identity

A request without a project starts in All Projects. `scope=all` overrides a
remembered project without writing that global preference. An explicit project
is resolved through the SDK's authorized provider using `remember=False`;
missing or unauthorized explicit selections return 403. The picker uses the
SDK's request-local `view_scope`/`view_project` attributes and the leaf updates
its URL on a real selection event.

The host supplies authorized volume roots. Displayed paths use a separate
`display_root` or `/home/<username>` label; this never changes real I/O roots.
Usage's sunburst scans the selected authorized root or exact project root.
Capacity cards are filesystem capacity, not claimed personal usage/quota.
Duplicates calls the real read-only content matcher, with a depth bound; missing
fclones is shown as unavailable rather than an empty successful report. The GUI
passes a 60-second deadline to the hashing subprocess itself; expiry kills and
reaps it instead of leaving it behind after a request timeout. A report with
more than 50 groups displays its actual total and labels the displayed subset.

## Logical tiers and estimates

`SCITEX_STORAGE_TIER_BINDINGS` maps a logical hot/warm/cool/cold name to one
volume provider key. Without a mapping, exactly one authorized volume declaring
that tier can be used. Missing or ambiguous tiers stay disabled. Malformed host mappings return a typed
503 response rather than a partial or misleading move view. Physical
archive host names are not an end-user tier selector. Remote archive transports
remain a separate CLI/Python operation; the browser requires mounted endpoints.

`SCITEX_STORAGE_TIER_RATES` optionally gives each tier a three-letter currency
and `per_gb_month` decimal rate. Estimates multiply selected bytes / 1,000,000,000
by that rate and days / 30 for 1, 7, and 30 days. No default commercial price is
invented; unconfigured rates say so. Transfer/service charges are excluded.

## Moves and recovery

Planning performs no writes. Applying is POST-only with an explicit confirmation,
both endpoint containment checks, and project write authorization. Hosted moves
refuse missing storage capabilities or incomplete project-root catalogs; a
read-only project produces 403. Registered
project roots cannot be relocated until a host project-location update port
exists. User-specific manifests come from the trusted host home provider, never
a browser absolute path or a process-global HOME substitution. The provider home
must be canonical and the resolved receipt path must stay inside it, including
when child directories are symbolic links.

The backend `plan_move` / `apply_move` transaction performs bounded rsync into a
private sibling, SHA checks source/copy/source, publishes without overwriting,
and stages the original for seven days. Source/destination paths may not overlap
the transaction state directory, so a move cannot relocate or copy its own journal. Timeouts cancel and reap the actual
rsync process group. Recorded intermediate phases are historical state, not proof
that a worker is still running; interrupted moves require recovery.

Public `restore_staged` restores a verified original without overwriting.
`sweep_restorable` is bounded and explicit: expired move originals require a
valid completed receipt and a current matching destination. Missing/corrupt
copies or exhausted budgets defer cleanup. This page schedules no cleanup and
performs no background operation merely because it was opened.

Regression evidence: `test_logical_move.py`, `test__move.py`,
`test__retention.py`, `test__logical_tiers.py`, and the scoped GUI tests.

Quickstart
==========

See `examples/quickstart.py <https://github.com/scitex-ai/scitex-storage/blob/develop/examples/quickstart.py>`_ for a runnable example.

.. literalinclude:: ../../examples/quickstart.py
   :language: python


Verified moves and retention
----------------------------

Archive moves use rsync checksums plus independent source/destination SHA256
verification. The original is staged restorable for seven days by default;
expiration alone never schedules deletion. ``sweep-restorable`` previews by
default and requires ``--yes`` to apply a bounded, checksum-guarded pass.
An explicit ``--dry-run`` takes precedence over ``--yes``.

The Python ``scitex_storage.restore_staged(staged_path)`` primitive restores
registered quarantine content without overwriting the original path. Pending
crash records can be recovered but cannot be swept. Legacy records without
content checksums require explicit investigation and remain retained.

Cleanup defaults to 32 records, 1 GiB, and 4096 filesystem entries per tree,
with a 30-second cooperative budget. The corresponding ``--max-*`` options
allow explicit changes; no sweeper job is enabled automatically. Blocking
filesystem calls remain an OS boundary.

The no-overwrite staging/restore primitive currently requires Linux
``renameat2(RENAME_NOREPLACE)``. Unsupported systems fail safely instead of
using an exists-then-rename fallback.


Local volume moves
------------------

``plan_move(source, destination, state_dir=...)`` plans an authorized local
file/directory move without writing state or hashing contents. The caller must
authorize source, destination, and the per-user runtime state directory.
``apply_move(plan, timeout_s=120, deadline=None, cancel_event=None)`` copies
with rsync, independently verifies content, publishes without replacing an
existing path, and retains the original for seven days.

``get_move_status(transaction_id, state_dir=...)`` reads the durable receipt.
Completed replays return the historical result. Other existing transactions
require explicit recovery; cancellation never deletes a published verified
copy. Job phases and a possible process PID remain recorded if the OS has not
yet acknowledged process-group termination. No worker or sweeper is detached,
and no project metadata or global project selection is rewritten.

The quarantine registry is ``state_dir.parent / "restorable-registry.json"``.
Pass it explicitly as ``registry_path`` to ``restore_staged`` or
``sweep_restorable`` when using owner-specific state. Do not substitute the
service process home for the authenticated owner's storage capability.


Before removing an expired MOVE original, cleanup checks its content, matching
completed receipt, and freshly verified destination content. Missing/corrupted
or unreachable targets retain the original. Remote archive targets require an
explicit bounded verifier; no provider means defer. Work budgets cover both
copies. Manually quarantined content requires the separate
``--allow-manual-quarantine`` opt-in as well as ``--yes``; expiration alone is
never permission to erase an unverified last copy.

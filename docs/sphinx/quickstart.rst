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

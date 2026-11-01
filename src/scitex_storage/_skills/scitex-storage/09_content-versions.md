---
description: |
  [TOPIC] Local content-addressed scientific-file versions
  [DETAILS] Bounded Gear CDC snapshots, verified no-overwrite checkout,
  and small ordinary Git pointers; not a Git LFS/server implementation.
tags: [scitex-storage-content-versions]
---

# Content versions

Use `VersionStore(root)` for additive local file snapshots. Construction and
`plan_ingest(source)` write nothing; the plan reads metadata only. The Python
`ingest(source)` call explicitly writes immutable chunks and a version manifest
while retaining the source. CLI `versions --store ROOT add FILE` previews;
`--yes` applies, and `--dry-run` always wins.

Keep ownership clear: Storage owns chunk/manifests; Git/Gitea owns the history
of small pointer files. `write_git_pointer(path, manifest)` creates a new
pointer only. `render_git_pointer(manifest)` supplies text for normal reviewed
Git pointer edits. Reuse existing `scitex-git` commit/remote APIs where needed;
Storage must not install Git filters, configure a server, or claim LFS
interoperability from an ordinary Git test.

`checkout(version_id, new_path)` and `checkout_pointer(pointer, new_path)` verify
chunks and the full output before publishing a new file. Existing files and
symlinks are refused. Do not work around that refusal by deleting or replacing
the destination. `read_manifest`/CLI `inspect` validate manifest metadata only;
they do not prove that the payload chunks are intact.

The algorithm is `gear-cdc-v1`, not full FastCDC. Source changes refuse a new
version; unreferenced chunks can remain. No GC or source-removal command exists.
A local snapshot does not authorize deleting the original or count as an
independent backup. Host integrations still own path authorization.

Implementation: `scitex_storage._versions`; CLI: `_cli/_versions_cmd.py`.
Tests: `tests/scitex_storage/_versions/` plus CLI tests. The detailed maintained
format/limitations are in the repository's `docs/content-versions.md`.

# Local content versions for scientific files

`VersionStore` snapshots regular files into a local immutable object store.
Storage owns the payload chunks and version manifests. Git owns the history of
small pointer files. Creating a version keeps the source in place; it is not
permission to retire the original or treat this single local copy as a backup.

This is an additive source preview. It does not configure Gitea, upload objects,
install Git clean/smudge filters, or implement the Git LFS protocol. Gitea's
standard LFS service is a separate, explicitly configured server feature.
See [Gitea's official LFS setup](https://docs.gitea.com/administration/git-lfs-setup/).
An ordinary SciTeX pointer can be committed to a Git/Gitea repository as text;
cloning that repository does **not** fetch the Storage object store.

## Python example

This complete example uses only its own temporary files:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from scitex_storage import VersionStore, write_git_pointer

with TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / "experiment.bin"
    source.write_bytes(b"instrument readings\n" * 10_000)
    store = VersionStore(root / "objects")
    plan = store.plan_ingest(source)  # stat only; does not create the store
    assert not store.root.exists()
    result = store.ingest(source)  # explicit additive write; source retained
    pointer = write_git_pointer(root / "experiment.stx.json", result.manifest)
    restored = store.checkout_pointer(pointer, root / "restored.bin")
    assert restored.read_bytes() == source.read_bytes()
```

`write_git_pointer` creates a new file and refuses every existing occupant.
`render_git_pointer(manifest)` returns bytes as a string for your repository's
normal reviewed editing/commit workflow when updating an already tracked
pointer. Storage does not run Git or duplicate `scitex-git`'s commit/remote APIs.
The pointer contains the version digest, full-file digest, size, and chunk
count; it contains no machine-specific object-store path.

## CLI

Choose an explicit store and inspect the JSON result. Mutating commands default
to preview; `--yes` applies the requested operation, while `--dry-run` takes
precedence over `--yes`.

```console
scitex-storage versions --store ./objects add ./experiment.bin
scitex-storage versions --store ./objects add ./experiment.bin --yes
```

Use `manifest.version_id` from the applied result as `VERSION_ID` below:

```console
scitex-storage versions --store ./objects inspect VERSION_ID
scitex-storage versions --store ./objects pointer VERSION_ID ./experiment.stx.json --yes
scitex-storage versions --store ./objects checkout VERSION_ID ./restored.bin --yes
```

The add preview reads file metadata only. Reuse/new-byte counts remain `null`
until ingestion measures them. Counts in the applied result concern payload
chunk bytes and exclude manifest/filesystem overhead. `inspect` verifies the
manifest, not chunk contents, and reports `content_verified: false` explicitly.
Checkout verifies each chunk and the reconstructed file before publishing.

## Format and boundaries

The `gear-cdc-v1` algorithm uses a fixed SHA256-derived Gear table and rolling
content fingerprint. Defaults are 64 KiB minimum, 256 KiB mask scale, and 1 MiB
maximum, with 64 KiB read blocks. The final remainder can be smaller than the
minimum. The average is a scale, not a promise of exact chunk sizes. I/O block
size does not change chunk boundaries or version identity.

This is bounded Gear-based content-defined chunking, **not full FastCDC**.
FastCDC combines additional cut-point and distribution-normalization techniques;
this implementation does not claim those optimizations or the paper's benchmark
results. See [the original USENIX FastCDC paper](https://www.usenix.org/conference/atc16/technical-sessions/presentation/xia).

Objects are laid out under the supplied root:

```text
chunks/sha256/<first-two-hex>/<chunk-sha256>
versions/sha256/<first-two-hex>/<manifest-sha256>
```

A version is streaming JSON Lines: one schema/chunking header, ordered chunk
references, and a footer with full-file SHA256, size, and reference count. Its
identifier is SHA256 of all manifest bytes. Chunking parameters are part of
version identity; read-block size, source filename, and timestamps are not.
Empty files have zero chunks and a valid full-file digest.

Both file chunking and manifest traversal use bounded working buffers; no list
of all chunk references is built. Manifests reject records over 4096 bytes,
invalid digests, unsupported schemas, and inconsistent counts/sizes. Physical
file size has no arbitrary total cap. The insertion regression demonstrates
more than 90% payload reuse on a deterministic random-byte fixture; actual reuse
depends on the bytes and format. Compressed/repacked scientific files can have
very different reuse characteristics. No compression-format-aware deduplication
is claimed.

## Safety and current limits

- Ingest accepts regular files, rejects symlink inputs and inputs inside the
  store, and checks device/inode/size/mtime/ctime before and after reading.
  An observed change refuses the version. This is a mutation detector, not a
  lock on uncooperative external writers. Optional `on_progress(done, total)`
  callbacks report bytes at chunk boundaries for ingestion/checkout; raising
  from a callback cancels before publication.
- Completed objects are flushed, made read-only, and atomically linked into
  place without overwriting. Existing objects are rehashed before reuse;
  corruption is an error, never permission to replace an object.
- Checkout checks destination free space, reconstructs into a private adjacent
  temporary file, and checks chunk and full-file digests. Atomic no-replace
  publication refuses files, broken symlinks, and racing occupants. The parent
  directory must already exist. Checkouts are new owner-private files, not
  restoration of original permissions/timestamps.
- Failed or interrupted ingests can leave unreferenced immutable chunks;
  incomplete manifest/checkout temporaries are cleaned up on ordinary failure.
  Process termination can leave temporary files. There is no default cleanup,
  garbage collector, source deletion, retention sweeper, or background service
  for this feature.
- The initial store requires a local POSIX filesystem with hard-link and fsync
  support. It has no remote transport, encryption, multi-user authorization,
  scientific-format parser, object packing, or directory snapshot protocol.
  Each unique chunk consumes a file/inode; account for inode quotas as well
  as bytes. A host integration
  must authorize its local paths independently.

## Measured prototype cost

One local helper-container run (Linux 6.8, CPython 3.12.13) ingested a fresh
32 MiB deterministic random-byte fixture in **12.10 seconds (2.65 MiB/s)**,
including immutable-object fsyncs. All 32 MiB were new payload, in 102 chunks;
verified checkout also passed. Whole-process peak RSS was **27.6 MiB**, about
2.8 MiB above the process's prior high-water mark. RSS includes the interpreter
and imports; that delta is not an exact measurement of algorithm-only memory.
The [raw sample](benchmarks/content-versions-local-32mib.json) records source-file
hashes and all measured fields.

This per-byte Python Gear loop is a correctness-focused prototype. The sample
does not qualify production terabyte throughput, concurrent workloads, remote
filesystems, or every scientific format. A native CDC engine is not included.
There is no claim of FastCDC-level performance.

Reproduce using only a fresh owned temporary fixture/store:

```sh
PYTHONPATH=src python scripts/benchmark-content-versions.py --size-mib 32
```

## Validation

```sh
PYTHONPATH=src python -m pytest tests/scitex_storage/_versions tests/scitex_storage/_cli/test__versions_cmd.py
```

These tests use real files and an isolated real Git repository. They cover
bounded reads, stable boundaries, insertion reuse, streaming manifest memory,
source mutation, corrupt objects, symlink/path rejection, no-overwrite races,
CLI dry runs, and two pointer commits whose corresponding versions both
restore. No server or LFS interoperability is inferred from the Git test.

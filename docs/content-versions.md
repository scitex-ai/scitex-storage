# Experimental local content versions for scientific files

`VersionStore` snapshots regular files into a local immutable object store.
Storage owns the payload chunks and version manifests. Git owns the history of
small pointer files. Creating a version keeps the source in place; it is not
permission to retire the original or treat this single local copy as a backup.

This is an experimental additive local-storage feature in 0.6.16. It does not
configure Gitea, upload objects,
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

The versioned algorithm `fastcdc-py-1.7.0-cython-v1` delegates cut points to
[fastcdc-py's published native Cython engine](https://github.com/iscc/fastcdc-py),
a FastCDC/ronomon variant. It does not claim exact equivalence to the original
[USENIX paper's implementation or benchmarks](https://www.usenix.org/conference/atc16/technical-sessions/presentation/xia).
The declared normal dependency is pinned to `fastcdc==1.7.0`; changing the engine
requires an explicit format decision and boundary qualification.

Defaults are **512 KiB minimum, 2 MiB average scale, and 8 MiB maximum**, with
64 KiB read blocks. The final remainder may be smaller than the minimum; actual
chunk sizes depend on content. I/O block size does not change boundaries or
version identity. Only bounded byte windows reach the native engine: its
path/file API, which maps the whole input, is deliberately not used. Working
memory scales with the maximum chunk size, including bounded window copies.

A compiled backend is required. Missing or unloadable native code raises
`NativeChunkerUnavailable` before ingest creates the store; it never silently
uses the provider's slow Python fallback. Normal CPython 3.12 Linux installation
used the published wheel. Normal CPython 3.13 installation built and loaded the
native extension from the package's source distribution. A platform without a
compatible wheel needs the normal Cython/compiler build prerequisites; a
successful pip exit alone does not prove native code was built. Metadata reads
and verified checkout do not require the chunking engine.

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
  Each unique chunk consumes a file/inode and each version references every
  chunk in its streaming manifest. At a nominal 2 MiB average, 1 TiB implies
  roughly 524,288 chunk references; actual object counts depend on content and
  deduplication. Smaller custom chunks increase inode, fsync and metadata costs.
  Manifest processing is bounded-memory but linear in reference count; there
  is no packed-object index or production million-object qualification.
  Host integrations must authorize local paths independently.

## Measured local cost

One local helper-container run (Linux 6.8, CPython 3.12.13) ingested a fresh
32 MiB deterministic random-byte fixture with the actual 2 MiB defaults in
**0.218 seconds (146.8 MiB/s)**, including immutable-object fsyncs. All 32 MiB
were new payload, in 18 chunks; verified checkout passed. CPU time was 0.142 s.
Whole-process peak RSS was **52.9 MiB**, about 29.2 MiB above its prior high-water
mark. RSS includes interpreter/imports; the delta is not an exact measurement
of algorithm-only memory. The [raw sample](benchmarks/content-versions-local-32mib.json)
records implementation hashes and measured fields.

This is one short local sample, not a throughput guarantee or qualification for
terabyte datasets, concurrent ingests, cold storage, remote filesystems or every
scientific format. The pre-native bounded Gear prototype measured 2.65 MiB/s
on its separate sample; that historical baseline is retained in
[the prototype record](benchmarks/content-versions-gear-prototype-32mib.json).
Its format was never shipped as a supported version-store format.

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

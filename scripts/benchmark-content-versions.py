#!/usr/bin/env python3
"""Measure a fresh local ingest using only an owned temporary binary fixture."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import random
import resource
import sys
import tempfile
import time
from pathlib import Path

import scitex_logging as slogging

from scitex_storage import VersionStore
from scitex_storage._versions import _store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, default=32)
    options = parser.parse_args()
    if options.size_mib <= 0:
        parser.error("--size-mib must be positive")
    with tempfile.TemporaryDirectory(prefix="scitex-content-benchmark-") as name:
        root = Path(name)
        source = root / "instrument.bin"
        generator = random.Random(20_261_102)
        fixture_hash = hashlib.sha256()
        with source.open("wb") as stream:
            for _ in range(options.size_mib * 16):
                block = generator.randbytes(64 * 1_024)
                fixture_hash.update(block)
                stream.write(block)
        store = VersionStore(root / "objects")
        gc.collect()
        units = 1 if sys.platform == "darwin" else 1_024
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * units
        started, cpu_started = time.perf_counter(), time.process_time()
        result = store.ingest(source)
        elapsed, cpu = time.perf_counter() - started, time.process_time() - cpu_started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * units
        store.checkout(result.manifest.version_id, root / "verified-checkout.bin")
        if result.manifest.file_sha256 != fixture_hash.hexdigest():
            raise RuntimeError("Fixture checksum differs from the ingested version")
        implementation = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in Path(_store.__file__).parent.glob("*.py")
        }
        payload = {
            "fixture_mib": options.size_mib,
            "verified_checkout": True,
            "implementation_sha256": implementation,
            "elapsed_seconds": elapsed,
            "cpu_seconds": cpu,
            "ingest_mib_per_second": options.size_mib / elapsed,
            "process_peak_rss_bytes": peak,
            "prior_process_peak_rss_bytes": before,
            "peak_rss_increase_above_prior_high_water_bytes": max(0, peak - before),
            "new_payload_bytes": result.new_bytes,
            "reused_payload_bytes": result.reused_bytes,
            "chunk_count": result.manifest.chunk_count,
            "algorithm": result.manifest.chunking.manifest_fields(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "limits": (
                "one local fresh-store sample; includes chunk fsync; "
                "RSS is whole-process high water; not TB throughput qualification"
            ),
        }
        slogging.getPlainConsole(__name__).emit(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()

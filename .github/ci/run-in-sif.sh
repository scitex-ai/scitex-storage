#!/usr/bin/env bash
# Runs INSIDE the reused scitex-ci SIF (apptainer exec). $1 = python version.
# Adapted from scitex-ssh's .github/ci/run-in-sif.sh (the fleet's established
# sibling convention) — package name / import path / cov target swapped for
# scitex-storage; logic otherwise unchanged.
#
# WHY a layered install (not the bare PYTHONPATH=src trick scitex-dev uses):
# the shared ci-cpu.sif bakes scitex-dev[all,dev] DEPS, NOT scitex-storage's.
# So we install THIS checkout + its [all,dev] extras (WITH dependency
# resolution) into a writable --target dir and prepend that on PYTHONPATH. The
# SIF still supplies the heavy shared base (pip/uv, the python interpreters,
# scitex-dev's deps), so only scitex-storage's own thin dep set is fetched per
# run.
#
# --target (not a plain `-e .`): the SIF's /opt/venv-* are root-owned + RO and
# the HPC compute-node HOME is RO inside the container, so a normal site install
# fails Permission denied. A writable target on node-local /tmp sidesteps both.
#
# Fail-loud: a missing interpreter or a failed install is a hard error.
set -euo pipefail

V="${1:?python version arg required (3.11/3.12/3.13)}"
VENV="/opt/venv-$V"
test -x "$VENV/bin/python" || {
    echo "::error::baked python missing in $VENV — rebuild the SIF: scitex-container apptainer build ci-cpu"
    exit 1
}

export LC_ALL=C.UTF-8 LANG=C.UTF-8

# Real writable scratch. The runner profile exports TMPDIR=~/.cache/tmp, a host
# path that does NOT resolve inside the container; tests (tmp_path) and the
# install target both need a working, writable tmp. Node-local /tmp is writable
# + ephemeral and per-version-isolated so concurrent matrix legs don't collide.
export TMPDIR="/tmp/ci-scitex_storage-${GITHUB_RUN_ID:-0}-${GITHUB_RUN_ATTEMPT:-0}-$V"
rm -rf "$TMPDIR"
mkdir -p "$TMPDIR/site" "$TMPDIR/uv-cache"

# The HPC compute-node $HOME is READ-ONLY inside the container, so uv/pip cannot
# create their default caches under ~/.cache — point them at the writable
# scratch instead (else `uv pip install` dies: "failed to create directory
# ~/.cache/uv: File exists / read-only").
export UV_CACHE_DIR="$TMPDIR/uv-cache"
export XDG_CACHE_HOME="$TMPDIR"
export PIP_CACHE_DIR="$TMPDIR/pip-cache"

# A VIRTUAL_ENV leaked from the runner profile (~/.env-3.11) is a broken symlink
# in here; unset it so no tool (uv, pip) tries to follow it.
unset VIRTUAL_ENV || true

# venv bin on PATH (this matrix leg's python3 + pip); PYTHONPATH points at the
# writable target so imports + coverage use the freshly-installed checkout.
export PATH="$VENV/bin:$PATH"

echo "py=$("$VENV/bin/python" -V) target=$TMPDIR/site"

# Install scitex-storage + its [all,dev] extras WITH deps into the writable
# target. Fallback chain so a packaging hiccup in an optional extra doesn't
# strand CI: [all,dev] → [dev] → bare. uv first (fast resolver), pip as a
# final safety net.
uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" -e ".[all,dev]" ||
    uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" -e ".[dev]" ||
    uv pip install --python "$VENV/bin/python" --target="$TMPDIR/site" -e "." ||
    pip install --target="$TMPDIR/site" -e ".[dev]"

export PYTHONPATH="$TMPDIR/site:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

# Parallelise with pytest-xdist if available (no-op fallback to plain -q if
# pytest-xdist isn't in scitex-storage's [dev] extras). Worker count: use ALL
# cores (each matrix leg runs on its own dedicated self-hosted node, no
# co-tenant). Floor 4.
NPROC="$(nproc 2>/dev/null || echo 4)"
WORKERS=$NPROC
[ "$WORKERS" -lt 4 ] && WORKERS=4
echo "xdist workers=$WORKERS (nproc=$NPROC)"

XDIST_ARGS=()
if python -c "import xdist" 2>/dev/null; then
    XDIST_ARGS=(-n "$WORKERS" --dist load)
else
    echo "pytest-xdist not importable — running single-process"
fi

# nice -n 19 ionice -c 3: lowest CPU + idle I/O priority so CI yields to any
# higher-priority process if the node is ever shared. exec replaces the shell so
# signals/exit code propagate to the runner step.
exec nice -n 19 ionice -c 3 \
    python -m pytest tests/ "${XDIST_ARGS[@]}" -q \
    --cov=src/scitex_storage --cov-report=xml --cov-report=term \
    -p no:cacheprovider

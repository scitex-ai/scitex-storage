"""CACHE class and the split COULD_NOT_LOOK states.

Every case here comes from paper-scitex-clew running the real detector
against 10 REAL capsule runtime trees on Spartan (2026-07-28, read-only).
The probe returned ZERO regenerable verdicts, from two causes -- one
correct, one a genuine gap:

* detected environments had no spec anywhere above them (correct: an
  environment whose recipe is missing IS the only copy), and
* the LARGEST trees were never detected at all -- `pylibs` (a
  `pip install --target` layout), `mamba` and `mmroot` (conda package
  caches holding only `pkgs/`), `.uvcache` (carrying `CACHEDIR.TAG`).

The conceptual gap those exposed: the model had two classes, environment
(needs a spec) and not-an-environment. Those trees are NEITHER. They are
CACHES, and a cache is regenerable with NO spec because the recipe is the
network. Requiring a spec of a cache is exactly as wrong as not requiring
one of an environment.

Real tmp_path trees throughout; no `monkeypatch`, which this repo bans.
"""

from __future__ import annotations

from pathlib import Path

from scitex_storage._measure._regenerable import (
    CACHE,
    CACHEDIR_TAG,
    NOT_REGENERABLE,
    PYCACHE_DIR,
    REASON_ABSENT,
    REASON_IS_FILE,
    REGENERABLE,
    detect_cache,
    detect_environment,
    is_regenerable,
)


# --- CACHEDIR.TAG ---------------------------------------------------------
def test_a_cachedir_tag_marks_a_cache(tmp_path):
    # The freedesktop standard: the writing tool DECLARES the tree
    # disposable, which beats anything we could infer.
    # Arrange
    cache = tmp_path / ".uvcache"
    cache.mkdir()
    (cache / CACHEDIR_TAG).write_text("Signature: 8a477f597d28d172789f06886806bc55\n")

    # Act
    kind, _marker = detect_cache(str(cache))

    # Assert
    assert kind == "cachedir-tag"


def test_a_cache_is_regenerable_WITHOUT_any_spec(tmp_path):
    # The whole point of the class. An environment here would be refused
    # for having no recipe; a cache's recipe is the network.
    # Arrange
    cache = tmp_path / ".uvcache"
    cache.mkdir()
    (cache / CACHEDIR_TAG).write_text("Signature: x\n")

    # Act
    verdict = is_regenerable(str(cache), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == CACHE


def test_the_cache_evidence_says_the_marker_was_the_authors_declaration(tmp_path):
    # Arrange
    cache = tmp_path / "c"
    cache.mkdir()
    (cache / CACHEDIR_TAG).write_text("Signature: x\n")

    # Act
    verdict = is_regenerable(str(cache), stop_at=str(tmp_path))

    # Assert
    assert "declaration" in verdict.evidence


# --- conda package cache --------------------------------------------------
def test_a_conda_root_holding_only_pkgs_is_a_cache(tmp_path):
    # Measured on real `mamba` and `mmroot` trees: they contain `pkgs` and
    # nothing else. Not an environment, not an install root -- a cache.
    # Arrange
    root = tmp_path / "mamba"
    (root / "pkgs").mkdir(parents=True)

    # Act
    kind, _marker = detect_cache(str(root))

    # Assert
    assert kind == "conda-pkgs"


def test_a_conda_root_with_MORE_than_pkgs_is_not_a_cache(tmp_path):
    # A real conda prefix has bin/, lib/, conda-meta/ -- that is an
    # environment and must go through the spec gate, not around it.
    # Arrange
    root = tmp_path / "prefix"
    (root / "pkgs").mkdir(parents=True)
    (root / "conda-meta").mkdir()
    (root / "bin").mkdir()

    # Act
    kind, _marker = detect_cache(str(root))

    # Assert
    assert kind is None


def test_a_real_conda_prefix_named_renv_is_still_conda(tmp_path):
    # The single best case in the whole probe, and it came from real data:
    # cohort_a_scitex/capsule-004/renv is structurally a full conda prefix.
    # Every name-based rule calls that R. Structure gets it right.
    # Arrange
    root = tmp_path / "renv"
    (root / "conda-meta").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "x86_64-conda-linux-gnu").mkdir()

    # Act
    ecosystem, _marker = detect_environment(str(root))

    # Assert
    assert ecosystem == "conda"


# --- pip install --target -------------------------------------------------
def test_a_dist_info_at_top_level_is_a_target_install(tmp_path):
    # PEP 376's installed-distribution marker -- as structural as
    # pyvenv.cfg. This shape (`pylibs`, `pylib`, `sdlib`) is the most
    # common environment in the capsule corpus and was invisible before.
    # Arrange
    target = tmp_path / "pylibs"
    (target / "wfdb").mkdir(parents=True)
    (target / "wfdb-4.3.1.dist-info").mkdir()

    # Act
    ecosystem, _marker = detect_environment(str(target))

    # Assert
    assert ecosystem == "python-target"


def test_a_target_install_STILL_needs_a_spec(tmp_path):
    # It is an environment with no recipe above it, not a cache. The
    # measured capsules have no requirements.txt anywhere up to `/`, so
    # this must stay refused rather than being swept in with the caches.
    # Arrange
    target = tmp_path / "pylibs"
    (target / "wfdb").mkdir(parents=True)
    (target / "wfdb-4.3.1.dist-info").mkdir()

    # Act
    verdict = is_regenerable(str(target), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_a_target_install_with_a_spec_is_regenerable(tmp_path):
    # Arrange
    target = tmp_path / "pylibs"
    (target / "wfdb").mkdir(parents=True)
    (target / "wfdb-4.3.1.dist-info").mkdir()
    (tmp_path / "requirements.txt").write_text("wfdb==4.3.1\n")

    # Act
    verdict = is_regenerable(str(target), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == REGENERABLE


# --- the split could-not-look states --------------------------------------
def test_a_file_and_an_absent_path_no_longer_share_a_reason(tmp_path):
    # They returned character-for-character identical strings before. A
    # deletion sweep must treat them OPPOSITELY.
    # Arrange
    a_file = tmp_path / "mm.tar.bz2"
    a_file.write_bytes(b"\x00")
    absent = tmp_path / "does-not-exist-98765"

    # Act
    file_reason = is_regenerable(str(a_file)).reason
    absent_reason = is_regenerable(str(absent)).reason

    # Assert
    assert file_reason != absent_reason


def test_a_tarball_is_reported_as_a_file_which_is_routine(tmp_path):
    # Arrange
    tarball = tmp_path / "capsule-004.tar.gz"
    tarball.write_bytes(b"\x1f\x8b")

    # Act
    verdict = is_regenerable(str(tarball))

    # Assert
    assert verdict.reason == REASON_IS_FILE


def test_an_absent_path_is_reported_as_absent_which_is_alarming(tmp_path):
    # Means the caller's inventory is stale: something already moved or was
    # deleted, and a sweep should stop and re-measure.
    # Arrange
    missing = tmp_path / "gone"

    # Act
    verdict = is_regenerable(str(missing))

    # Assert
    assert verdict.reason == REASON_ABSENT


def test_the_absent_evidence_tells_the_caller_to_stop(tmp_path):
    # Arrange
    missing = tmp_path / "gone"

    # Act
    verdict = is_regenerable(str(missing))

    # Assert
    assert "stop and" in verdict.evidence


def test_the_file_evidence_tells_the_caller_it_may_continue(tmp_path):
    # Arrange
    a_file = tmp_path / "x.bin"
    a_file.write_bytes(b"\x00")

    # Act
    verdict = is_regenerable(str(a_file))

    # Assert
    assert "may skip it and continue" in verdict.evidence


# --- the never-touch trees still refuse -----------------------------------
def test_a_records_directory_is_still_not_regenerable(tmp_path):
    # Confirmed 100% across all 10 real capsules: submission/, sessions/,
    # scripts/, config/, results/, .scitex/ all refuse. They pass for a
    # THIN reason -- absence of a marker -- so the caller's success gate
    # stays the load-bearing guard. Structure alone is not a policy.
    # Arrange
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "submission.json").write_text("{}")

    # Act
    verdict = is_regenerable(str(submission), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_a_cache_marker_inside_a_records_dir_is_the_known_risk(tmp_path):
    # Documents the limit rather than pretending it away: a CACHEDIR.TAG
    # dropped inside submission/ WOULD flip it to CACHE. Structure cannot
    # know the tree is a record. This is exactly why the success gate and
    # never-touch list stay caller-side policy.
    # Arrange
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / CACHEDIR_TAG).write_text("Signature: x\n")

    # Act
    verdict = is_regenerable(str(submission), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == CACHE


# --- __pycache__: one mandated name, and only because it is mandated ------
def test_a_pycache_holding_pyc_files_is_a_cache(tmp_path):
    # The consumer's old name-list caught these and the structural rule did
    # not, which was a real regression they reported rather than worked
    # around. Admissible because CPython writes this exact name by language
    # specification -- a protocol constant, not a site convention.
    # Arrange
    pycache = tmp_path / "pkg" / PYCACHE_DIR
    pycache.mkdir(parents=True)
    (pycache / "mod.cpython-312.pyc").write_bytes(b"\xcb\x0d\x0d\x0a")

    # Act
    kind, _marker = detect_cache(str(pycache))

    # Assert
    assert kind == "pycache"


def test_a_pycache_is_regenerable_with_NO_spec(tmp_path):
    # Same reason as the other caches: the recipe is not a file we must
    # find, it is the interpreter plus the source sitting beside it.
    # Arrange
    pycache = tmp_path / "pkg" / PYCACHE_DIR
    pycache.mkdir(parents=True)
    (pycache / "mod.cpython-312.pyc").write_bytes(b"\xcb\x0d\x0d\x0a")

    # Act
    verdict = is_regenerable(str(pycache), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == CACHE


def test_the_NAME_alone_does_not_make_a_cache(tmp_path):
    # THE test that keeps this from becoming the name-matching this module
    # exists to replace. A human can create a directory called
    # `__pycache__` and put irreplaceable data in it; the name is then a
    # lie and the contents are the truth. Content must corroborate.
    # Arrange
    impostor = tmp_path / PYCACHE_DIR
    impostor.mkdir()
    (impostor / "measurements.csv").write_text("subject,value\n001,42\n")

    # Act
    kind, _marker = detect_cache(str(impostor))

    # Assert
    assert kind is None


def test_pyc_files_OUTSIDE_a_pycache_dir_are_not_a_cache(tmp_path):
    # The converse guard. Loose `.pyc` files next to real work (the legacy
    # Python 2 layout, or a shipped bytecode-only package) must not drag a
    # whole directory into the disposable class.
    # Arrange
    src = tmp_path / "legacy"
    src.mkdir()
    (src / "mod.pyc").write_bytes(b"\xcb\x0d\x0d\x0a")
    (src / "mod.py").write_text("x = 1\n")

    # Act
    kind, _marker = detect_cache(str(src))

    # Assert
    assert kind is None


def test_a_dot_cache_without_the_tag_is_KEPT_which_under_reclaims(tmp_path):
    # Pins the carve-out as a DECISION rather than letting it look like an
    # oversight someone should later "fix". Measured 2026-07-28: uv writes
    # CACHEDIR.TAG (so it is already caught), pip and huggingface do not.
    # Their locations are redirectable via XDG_CACHE_HOME / PIP_CACHE_DIR /
    # HF_HOME, so the name is a guess about the directory rather than a
    # fact about it. Keeping them costs disk; clearing them on a guess can
    # cost the only copy.
    # Arrange
    cache = tmp_path / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    (cache / "blob").write_bytes(b"\x00" * 16)

    # Act
    verdict = is_regenerable(str(cache), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE

# EOF

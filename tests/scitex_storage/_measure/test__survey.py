"""The composition layer: run the per-tree probes and combine them.

Layer 1's signals were all built and merged, `classify()` combined them,
and NOTHING RAN THE PROBES -- so every caller had to know which signal
functions exist, what each needs, and which apply to a bare directory at
all. Complete and unusable, the same way the regenerability detector was
complete and unreachable before it got a CLI verb.

The tests pin the two things that make this layer correct rather than
merely present: that an unreadable subtree REFUSES a verdict instead of
quietly answering about the readable fraction, and that a probe which
cannot run never produces the reassuring answer.

Real tmp_path trees throughout; no `monkeypatch`, which this repo bans.
"""

from __future__ import annotations

import os
import time

from scitex_storage._measure._classify import COULD_NOT_LOOK
from scitex_storage._measure._survey import (
    DEFAULT_COLD_AFTER_SECONDS,
    coverage_signal,
    stat_tree,
    survey,
)


def _tree(root, name="t", files=3):
    d = root / name
    d.mkdir(parents=True)
    for i in range(files):
        (d / f"f{i}.txt").write_text(f"x{i}\n")
    return d


# --- stat_tree records BOTH timestamps ----------------------------------
def test_stat_tree_records_the_newest_mtime(tmp_path):
    # Arrange
    d = _tree(tmp_path)

    # Act
    stats = stat_tree(str(d))

    # Assert
    assert stats.newest_mtime is not None


def test_stat_tree_records_the_newest_atime_too(tmp_path):
    # A READER LEAVES NO MTIME. Without atime, a corpus read daily is
    # byte-identical to an abandoned one -- the mistake that nearly cost a
    # 187 GiB tree read 11 hours before it was proposed for deletion.
    # Arrange
    d = _tree(tmp_path)

    # Act
    stats = stat_tree(str(d))

    # Assert
    assert stats.newest_atime is not None


def test_an_empty_tree_reports_None_not_zero(tmp_path):
    # Zero is a real epoch timestamp meaning "touched in 1970", which a
    # coldness test happily calls COLD. A probe that measured nothing must
    # not produce the answer the caller was hoping for.
    # Arrange
    empty = tmp_path / "empty"
    empty.mkdir()

    # Act
    stats = stat_tree(str(empty))

    # Assert
    assert stats.newest_mtime is None


def test_an_unreadable_subdirectory_is_counted_not_swallowed(tmp_path):
    # `os.walk` suppresses errors by default, so a walk over a tree with an
    # unreadable subdir returns a confident answer about a FRACTION of it.
    # Arrange
    d = _tree(tmp_path)
    locked = d / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("x\n")
    os.chmod(locked, 0o000)

    # Act
    stats = stat_tree(str(d))
    os.chmod(locked, 0o755)  # restore so tmp_path cleanup can run

    # Assert
    assert stats.unreadable_dirs > 0


# --- coverage refuses rather than warns ---------------------------------
def test_incomplete_coverage_refuses_a_verdict(tmp_path):
    # S5's doctrine applied to one tree: while part of the map is missing,
    # a verdict about the whole is a guess wearing a number. It REFUSES
    # rather than warning, because a confident reasoner walks past a
    # warning -- which is what happened for five hours while 681 GB sat
    # behind a permission stub.
    # Arrange
    d = _tree(tmp_path)
    locked = d / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    stats = stat_tree(str(d))
    os.chmod(locked, 0o755)

    # Act
    signal = coverage_signal(stats)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_the_coverage_refusal_does_not_claim_something_is_wrong(tmp_path):
    # It claims the map is INCOMPLETE, which is a weaker and far more
    # defensible claim than "this tree is suspicious".
    # Arrange
    d = _tree(tmp_path)
    locked = d / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    stats = stat_tree(str(d))
    os.chmod(locked, 0o755)

    # Act
    signal = coverage_signal(stats)

    # Assert
    assert "incomplete" in signal.evidence


def test_a_fully_read_tree_reports_complete_coverage(tmp_path):
    # POSITIVE CONTROL on the coverage signal. If it refused everything,
    # the refusal test above would pass for the wrong reason and the whole
    # classifier would abstain forever -- "safe but useless", which this
    # package already has a card about.
    # Arrange
    d = _tree(tmp_path)
    stats = stat_tree(str(d))

    # Act
    signal = coverage_signal(stats)

    # Assert
    assert signal.verdict != COULD_NOT_LOOK


def test_an_empty_tree_walks_cleanly_and_sees_no_files(tmp_path):
    # THE PREMISE for the refusal tests below, kept as its own test so
    # those can hold a single assertion each. It pins the state that makes
    # the zero-file case dangerous: the walk SUCCEEDS -- nothing is
    # unreadable, nothing is suppressed -- and still sees nothing. Without
    # this pinned, a later change that made empty trees report an error
    # would turn those tests green for a reason unrelated to what they
    # claim to prove.
    # Arrange
    empty = tmp_path / "unmounted"
    empty.mkdir()

    # Act
    stats = stat_tree(str(empty))

    # Assert
    assert (stats.file_count, stats.unreadable_dirs) == (0, 0)


def test_a_walk_that_read_zero_files_is_a_could_not_look(tmp_path):
    # THE UNMOUNTED MOUNT POINT. This walk succeeds, suppresses nothing,
    # and reads zero files -- and the old code called that MOVABLE, on the
    # evidence "read every entry it encountered (0 files)". A count of zero
    # over an empty denominator is not a clean result; it is no result
    # wearing a clean result's clothes.
    #
    # This is not an exotic case for a package that manages three NAS
    # units: a NAS that is not currently attached presents as exactly this
    # -- a readable, error-free, empty directory. Rendering it MOVABLE
    # points the classifier at the one answer that loses data, and does so
    # precisely when the data is invisible rather than absent.
    # Arrange
    empty = tmp_path / "unmounted"
    empty.mkdir()
    stats = stat_tree(str(empty))

    # Act
    signal = coverage_signal(stats)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_the_zero_file_refusal_names_the_mount_it_could_be(tmp_path):
    # A refusal that does not say what to check is a dead end. The caller
    # needs the next step -- confirm the filesystem is mounted -- because
    # the two states this cannot distinguish have opposite consequences.
    # Arrange
    empty = tmp_path / "unmounted"
    empty.mkdir()
    stats = stat_tree(str(empty))

    # Act
    signal = coverage_signal(stats)

    # Assert
    assert "mount" in signal.evidence.lower()


def test_a_tree_of_only_empty_subdirectories_sees_no_files(tmp_path):
    # Premise for the test below: a hollow tree walks successfully and
    # VISITS several entries, yet yields no file evidence.
    # Arrange
    d = tmp_path / "hollow"
    (d / "a" / "b").mkdir(parents=True)
    (d / "c").mkdir()

    # Act
    stats = stat_tree(str(d))

    # Assert
    assert (stats.file_count, stats.unreadable_dirs) == (0, 0)


def test_a_tree_with_only_empty_subdirectories_still_refuses(tmp_path):
    # The zero-file check must key on FILES READ, not on whether the walk
    # visited any directories -- otherwise a tree of empty subdirectories
    # would pass for inspected while yielding exactly as little evidence
    # as the bare empty dir.
    # Arrange
    d = tmp_path / "hollow"
    (d / "a" / "b").mkdir(parents=True)
    (d / "c").mkdir()
    stats = stat_tree(str(d))

    # Act
    signal = coverage_signal(stats)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


# --- survey composes, and refuses when it must --------------------------
def test_surveying_an_absent_path_is_a_could_not_look(tmp_path):
    # Not a verdict about the tree -- the ABSENCE of one.
    # Arrange
    missing = tmp_path / "gone"

    # Act
    result = survey(str(missing))

    # Assert
    assert result.verdict == COULD_NOT_LOOK


def test_surveying_a_readable_tree_returns_every_signal(tmp_path):
    # The point of the layer: a caller gets the composed verdict without
    # knowing which probes exist or what each one needs.
    # Arrange
    d = _tree(tmp_path)

    # Act
    result = survey(str(d), now=time.time())

    # Assert
    assert {s.name for s in result.signals} >= {"coverage", "coldness"}


def test_a_freshly_written_tree_is_not_cold(tmp_path):
    # POSITIVE CONTROL on coldness. If the signal always said "cold", every
    # tree on the fleet would be a deletion candidate and the test above
    # would still pass.
    # Arrange
    d = _tree(tmp_path)

    # Act
    result = survey(str(d), now=time.time(), cold_after_seconds=DEFAULT_COLD_AFTER_SECONDS)
    coldness = next(s for s in result.signals if s.name == "coldness")

    # Assert
    assert coldness.verdict != "movable"


def test_an_unreadable_subtree_makes_the_whole_verdict_refuse(tmp_path):
    # The composition must not average a refusal away. combine() is
    # deliberately not a vote, and this proves the refusal survives being
    # combined with signals that are perfectly happy.
    # Arrange
    d = _tree(tmp_path)
    locked = d / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)

    # Act
    result = survey(str(d), now=time.time())
    os.chmod(locked, 0o755)

    # Assert
    assert result.verdict == COULD_NOT_LOOK


def test_the_open_handle_scan_runs_with_its_own_positive_control(tmp_path):
    # survey() opens its own control file rather than trusting a caller to
    # remember. An empty /proc scan and a BLIND one are indistinguishable,
    # and the blind one returns "nothing is holding this" -- the answer the
    # caller wanted. Without a control the scan cannot be believed at all.
    # Arrange
    d = _tree(tmp_path)

    # Act
    result = survey(str(d), now=time.time())

    # Assert
    assert any(s.name == "open-handles" for s in result.signals)

# EOF

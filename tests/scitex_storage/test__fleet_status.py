"""Unit tests for scitex_storage._fleet_status (model + gatherer + renderer).

NO MOCKS (PA-306), and the module is shaped so none are needed. The
renderer is a pure function over dataclasses, so every case -- flag
thresholds, the three-state verdicts, dark mode -- is exercised by
constructing plain dataclass values, which is data, not a fake. The
gatherer is exercised against the REAL local filesystem (a real statvfs
on tmp_path, a really-missing path) -- no network, no ssh, no fakes.

Each test makes exactly one assertion (STX-TQ007): the cases pinned
hardest are the ones a naive dashboard gets wrong in the direction of
false reassurance -- a filesystem that could not be read must render
distinctly (grey em dash), never as a green 0%, and must be counted apart
from both healthy and flagged rows.
"""

from scitex_storage._fleet_status import (
    COULD_NOT_LOOK,
    DEFAULT_ROLES,
    MEASURED,
    NOT_APPLICABLE,
    UNKNOWN_ROLE,
    FleetSnapshot,
    HostStorage,
    build_dashboard_html,
    demo_snapshot,
    gather_fleet_snapshot,
    role_for,
    space_used_pct_from_counts,
)
from scitex_storage._fleet_status import _host_roles


def _measured(**kw) -> HostStorage:
    base = dict(host="h", role="tier1", mount="/m", verdict=MEASURED)
    base.update(kw)
    return HostStorage(**base)


# --------------------------------------------------------------------------
# HostStorage flag predicates.
# --------------------------------------------------------------------------


def test_space_flag_fires_at_the_threshold():
    # Arrange -- exactly 85%.
    row = _measured(used_pct=85.0)
    # Act
    flagged = row.space_flagged
    # Assert
    assert flagged is True


def test_space_flag_is_quiet_below_the_threshold():
    # Arrange
    row = _measured(used_pct=84.9)
    # Act
    flagged = row.space_flagged
    # Assert
    assert flagged is False


def test_inode_flag_fires_at_the_threshold():
    # Arrange
    row = _measured(inode_used_pct=97.0)
    # Act
    flagged = row.inode_flagged
    # Assert
    assert flagged is True


def test_a_row_is_flagged_when_only_space_is_over():
    # Arrange -- 96% space, 19% inodes (the ywata-note-win case).
    row = _measured(used_pct=96.0, inode_used_pct=19.0)
    # Act
    flagged = row.is_flagged
    # Assert
    assert flagged is True


def test_a_row_is_flagged_when_only_inodes_are_over():
    # Arrange -- 71% space, 97% inodes (the punim0264 case).
    row = _measured(used_pct=71.0, inode_used_pct=97.0)
    # Act
    flagged = row.is_flagged
    # Assert
    assert flagged is True


def test_a_healthy_row_is_not_flagged():
    # Arrange
    row = _measured(used_pct=27.0, inode_used_pct=2.0)
    # Act
    flagged = row.is_flagged
    # Assert
    assert flagged is False


def test_a_could_not_look_inode_never_flags():
    # Arrange -- an unknown is not an alarm.
    row = HostStorage(host="nas", role="tier1", mount="/v", verdict=COULD_NOT_LOOK,
                      used_pct=50.0, inode_used_pct=None)
    # Act
    flagged = row.inode_flagged
    # Assert
    assert flagged is False


def test_could_not_look_predicate_reads_the_verdict():
    # Arrange
    row = HostStorage(host="nas", role="tier1", mount="/v", verdict=COULD_NOT_LOOK)
    # Act
    verdict_blind = row.could_not_look
    # Assert
    assert verdict_blind is True


# --------------------------------------------------------------------------
# space_used_pct_from_counts -- the pure decision layer.
# --------------------------------------------------------------------------


def test_space_pct_matches_df_semantics():
    # Arrange -- 100 total, 40 free-to-root, 30 available-to-user: df counts
    # used/(used+avail) = 60/90.
    # Act
    pct = space_used_pct_from_counts(100, 40, 30)
    # Assert
    assert round(pct, 1) == 66.7


def test_space_pct_is_none_for_a_pseudo_filesystem():
    # Arrange -- zero blocks (a pseudo-fs) must not render as 0% used.
    # Act
    pct = space_used_pct_from_counts(0, 0, 0)
    # Assert
    assert pct is None


def test_space_pct_is_full_when_nothing_is_available():
    # Arrange -- every usable block is used.
    # Act
    pct = space_used_pct_from_counts(100, 0, 0)
    # Assert
    assert pct == 100.0


# --------------------------------------------------------------------------
# Role resolution.
# --------------------------------------------------------------------------


def test_role_for_uses_an_explicit_table():
    # Arrange
    table = {"spartan": "compute/tier1"}
    # Act
    role = role_for("spartan", table)
    # Assert
    assert role == "compute/tier1"


def test_role_for_returns_unknown_for_an_unregistered_host():
    # Arrange -- a guessed tier would be worse than an honest "?".
    # Act
    role = role_for("mystery-box", {})
    # Assert
    assert role == UNKNOWN_ROLE


def test_host_roles_falls_back_to_the_default_map():
    # Arrange -- with no registry role attribute yet, the defaults stand.
    # Act
    roles = _host_roles()
    # Assert -- every seeded fleet host resolves to SOMETHING, never crashes.
    assert set(DEFAULT_ROLES).issubset(roles)


# --------------------------------------------------------------------------
# gather_fleet_snapshot -- the I/O layer, against the real local filesystem.
# --------------------------------------------------------------------------


def test_gather_measures_a_real_local_path(tmp_path):
    # Arrange -- a real directory on whatever filesystem the test host uses.
    # Act
    row = gather_fleet_snapshot([str(tmp_path)]).rows[0]
    # Assert -- both MEASURED and NOT_APPLICABLE (btrfs/ZFS) are valid.
    assert row.verdict in (MEASURED, NOT_APPLICABLE)


def test_gather_reports_a_non_null_space_percentage(tmp_path):
    # Arrange -- a real filesystem always has a measurable space figure.
    # Act
    row = gather_fleet_snapshot([str(tmp_path)]).rows[0]
    # Assert
    assert row.used_pct is not None


def test_gather_space_percentage_is_within_bounds(tmp_path):
    # Arrange
    # Act
    row = gather_fleet_snapshot([str(tmp_path)]).rows[0]
    # Assert -- a real filesystem is somewhere in [0, 100].
    assert 0.0 <= row.used_pct <= 100.0


def test_gather_reports_could_not_look_for_a_missing_path(tmp_path):
    # Arrange
    missing = tmp_path / "definitely-not-here"
    # Act
    row = gather_fleet_snapshot([str(missing)]).rows[0]
    # Assert -- NOT a green 0%; a wedged/missing mount is its own state.
    assert row.verdict == COULD_NOT_LOOK


def test_gather_does_not_invent_a_zero_space_for_a_missing_path(tmp_path):
    # Arrange
    missing = tmp_path / "definitely-not-here"
    # Act
    row = gather_fleet_snapshot([str(missing)]).rows[0]
    # Assert
    assert row.used_pct is None


def test_gather_does_not_invent_a_zero_inode_for_a_missing_path(tmp_path):
    # Arrange
    missing = tmp_path / "definitely-not-here"
    # Act
    row = gather_fleet_snapshot([str(missing)]).rows[0]
    # Assert
    assert row.inode_used_pct is None


def test_gather_returns_one_row_per_path(tmp_path):
    # Arrange
    a = tmp_path / "a"
    a.mkdir()
    # Act
    snap = gather_fleet_snapshot([str(a), str(tmp_path / "nope")])
    # Assert
    assert snap.total_filesystems == 2


# --------------------------------------------------------------------------
# demo_snapshot -- exercises every rendering case.
# --------------------------------------------------------------------------


def test_demo_snapshot_has_the_seven_seed_rows():
    # Arrange
    # Act
    snap = demo_snapshot()
    # Assert
    assert snap.total_filesystems == 7


def test_demo_snapshot_covers_all_three_verdicts():
    # Arrange
    # Act
    verdicts = {r.verdict for r in demo_snapshot().rows}
    # Assert
    assert verdicts == {MEASURED, COULD_NOT_LOOK, NOT_APPLICABLE}


def test_demo_snapshot_counts_two_could_not_look_filesystems():
    # Arrange -- nas + nas1.
    # Act
    snap = demo_snapshot()
    # Assert
    assert snap.could_not_look_count == 2


def test_demo_snapshot_flags_the_space_and_inode_emergencies():
    # Arrange -- ywata-note-win (96% space) + punim0264 (97% inodes).
    # Act
    snap = demo_snapshot()
    # Assert
    assert snap.flagged_count == 2


# --------------------------------------------------------------------------
# build_dashboard_html -- the pure renderer.
# --------------------------------------------------------------------------


def test_dashboard_is_a_complete_document():
    # Arrange
    # Act
    html = build_dashboard_html(demo_snapshot())
    # Assert -- a complete page, not a fragment.
    assert html.startswith("<!doctype html>")


def test_dashboard_references_no_external_assets():
    # Arrange
    # Act
    html = build_dashboard_html(demo_snapshot())
    # Assert -- self-contained: no http(s) references to fetch.
    assert "http" not in html


def test_dashboard_is_dark_mode_by_default():
    # Arrange -- the operator's eyes are light-sensitive (constitution).
    # Act
    html = build_dashboard_html(demo_snapshot())
    # Assert -- the dark background variable is present in the inline CSS.
    assert "--bg: #12151a" in html


def test_dashboard_flags_an_over_threshold_row_red():
    # Arrange -- a single 96% row.
    snap = FleetSnapshot(rows=[_measured(host="w", used_pct=96.0, inode_used_pct=1.0)])
    # Act
    html = build_dashboard_html(snap)
    # Assert -- the flagged row class is emitted.
    assert 'tr class="flagged"' in html


def test_dashboard_does_not_flag_a_healthy_row():
    # Arrange
    snap = FleetSnapshot(rows=[_measured(host="w", used_pct=20.0, inode_used_pct=1.0)])
    # Act
    html = build_dashboard_html(snap)
    # Assert
    assert 'tr class="flagged"' not in html


def test_dashboard_renders_could_not_look_inode_as_an_em_dash():
    # Arrange -- the whole point: never a reassuring 0% for an unread mount.
    row = HostStorage(host="nas", role="tier1", mount="/v", verdict=COULD_NOT_LOOK,
                      used_pct=77.0, inode_used_pct=None)
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=[row]))
    # Assert
    assert "&mdash;" in html


def test_dashboard_labels_a_could_not_look_verdict():
    # Arrange
    row = HostStorage(host="nas", role="tier1", mount="/v", verdict=COULD_NOT_LOOK,
                      used_pct=77.0, inode_used_pct=None)
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=[row]))
    # Assert
    assert "could-not-look" in html


def test_dashboard_marks_a_not_applicable_inode_distinctly():
    # Arrange -- APFS: dynamic inode table, cannot run out.
    row = HostStorage(host="mba", role="workstation", mount="/v",
                      verdict=NOT_APPLICABLE, used_pct=62.0, inode_used_pct=None)
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=[row]))
    # Assert
    assert "not-applicable" in html


def test_dashboard_header_names_the_could_not_look_count():
    # Arrange
    # Act
    html = build_dashboard_html(demo_snapshot())
    # Assert -- the header must not silently disagree with the table.
    assert "Could not look" in html


def test_dashboard_header_names_the_flagged_count():
    # Arrange
    # Act
    html = build_dashboard_html(demo_snapshot())
    # Assert
    assert "Flagged" in html


def test_dashboard_renders_the_first_role_group():
    # Arrange -- two roles.
    rows = [_measured(host="a", role="tier1"), _measured(host="b", role="tier2")]
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=rows))
    # Assert
    assert "tier1" in html


def test_dashboard_renders_the_second_role_group():
    # Arrange -- two roles.
    rows = [_measured(host="a", role="tier1"), _measured(host="b", role="tier2")]
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=rows))
    # Assert
    assert "tier2" in html


def test_dashboard_does_not_leak_raw_note_markup():
    # Arrange -- a note carrying HTML-special characters must not break out.
    row = _measured(host="h", note="<script>alert(1)</script>")
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=[row]))
    # Assert
    assert "<script>alert(1)</script>" not in html


def test_dashboard_escapes_note_markup():
    # Arrange
    row = _measured(host="h", note="<script>alert(1)</script>")
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=[row]))
    # Assert
    assert "&lt;script&gt;" in html


def test_dashboard_renders_an_empty_snapshot_without_crashing():
    # Arrange -- zero rows is a legitimate (if uninteresting) snapshot.
    # Act
    html = build_dashboard_html(FleetSnapshot(rows=[]))
    # Assert
    assert "No filesystems" in html


def test_flagged_count_ignores_a_could_not_look_row():
    # Arrange -- a could-not-look row is an unknown, not an alarm.
    rows = [HostStorage(host="nas", role="t", mount="/v", verdict=COULD_NOT_LOOK,
                        used_pct=None, inode_used_pct=None)]
    # Act
    snap = FleetSnapshot(rows=rows)
    # Assert
    assert snap.flagged_count == 0


def test_could_not_look_count_sees_a_could_not_look_row():
    # Arrange
    rows = [HostStorage(host="nas", role="t", mount="/v", verdict=COULD_NOT_LOOK,
                        used_pct=None, inode_used_pct=None)]
    # Act
    snap = FleetSnapshot(rows=rows)
    # Assert
    assert snap.could_not_look_count == 1


# EOF

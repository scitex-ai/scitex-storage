"""Unit tests for the fleet-dashboard cache-read helper.

The Django ``fleet`` view is a one-liner over
``fleet_html_or_placeholder``, which is where all the behaviour lives:
serve the cached render when present, a named placeholder when absent.
The helper takes the path explicitly, so both branches are tested with a
REAL file under tmp_path -- no Django, no env, no mocks.

The view must NEVER gather live (that ssh-probes six hosts, ~90s, and
would hang the request), which is why the heavy work is out of band and
the view only reads.
"""

from __future__ import annotations

from scitex_storage._observe import fleet_html_or_placeholder


def test_absent_snapshot_yields_a_placeholder_not_an_exception(tmp_path):
    # "not gathered yet" is a real first-run state.
    # Act
    html = fleet_html_or_placeholder(str(tmp_path / "does-not-exist.html"))

    # Assert
    assert "No fleet snapshot" in html


def test_absent_snapshot_names_the_command_to_fix_it(tmp_path):
    # A dead end helps nobody; the placeholder must say what to run.
    # Act
    html = fleet_html_or_placeholder(str(tmp_path / "nope.html"))

    # Assert
    assert "fleet-status" in html


def test_a_present_snapshot_is_returned_verbatim(tmp_path):
    # Arrange
    snap = tmp_path / "fleet.html"
    snap.write_text("<html><body>FLEET-SENTINEL</body></html>", encoding="utf-8")

    # Act
    html = fleet_html_or_placeholder(str(snap))

    # Assert
    assert "FLEET-SENTINEL" in html

# EOF

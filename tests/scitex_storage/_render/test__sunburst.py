"""Unit tests for scitex_storage._render (Codecov-style sunburst).

Pure renderer over dataclasses -- no mocks, no I/O. The hierarchy shape
and the two colour metrics (space% and inode%) are the substance, plus
the self-contained / no-tag-breakout guarantees shared with the bubble
view.
"""

from __future__ import annotations

import pytest

from scitex_storage._fleet_status import MEASURED, FleetSnapshot, HostStorage
from scitex_storage._render import build_hierarchy, build_sunburst_html

GB = 1024**3


def _snap(rows):
    return FleetSnapshot(rows=rows, generated_at="t", note="n")


def _row(host, mount, size_gb, space, inode):
    return HostStorage(host=host, role="r", mount=mount, verdict=MEASURED,
                       size_bytes=size_gb * GB, used_pct=space, inode_used_pct=inode)


def test_hierarchy_root_is_the_fleet():
    # Arrange
    # Act
    root = build_hierarchy(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert root["name"] == "Fleet"


def test_hosts_are_the_first_ring():
    # Arrange
    # Act
    root = build_hierarchy(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert root["children"][0]["name"] == "h"


def test_filesystems_are_the_second_ring():
    # Arrange
    # Act
    root = build_hierarchy(_snap([_row("h", "/data", 10, 50.0, 5.0)]))
    # Assert
    assert root["children"][0]["children"][0]["name"] == "/data"


def test_a_segments_value_is_its_capacity_for_the_angle():
    # Arrange
    # Act
    root = build_hierarchy(_snap([_row("h", "/", 40, 50.0, 5.0)]))
    # Assert
    assert root["children"][0]["value"] == 40 * GB


def test_a_node_carries_both_colour_metrics():
    # The operator wants to switch colour between space and inodes.
    # Arrange
    # Act
    fs = build_hierarchy(_snap([_row("h", "/", 10, 63.0, 12.0)]))["children"][0]["children"][0]
    # Assert
    assert (fs["usage"], fs["inode"]) == (63.0, 12.0)


def test_host_inode_is_the_worst_of_its_filesystems_not_the_average():
    # punim0264: one fileset at 97% inodes is the alarm even if others are
    # low. A mean would hide it; the max surfaces it.
    # Arrange
    rows = [
        _row("spartan", "/a", 10, 50.0, 20.0),
        _row("spartan", "/b", 10, 50.0, 97.0),
    ]
    # Act
    host = build_hierarchy(_snap(rows))["children"][0]
    # Assert
    assert host["inode"] == 97.0


def test_the_page_fetches_no_external_resource():
    # The SVG namespace URI (createElementNS) is an identifier, never
    # fetched; a real external dependency would be a src=/href= to a URL.
    # Arrange
    # Act
    page = build_sunburst_html(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert 'src="http' not in page and 'href="http' not in page


def test_both_colour_mode_buttons_are_present():
    # Arrange
    # Act
    page = build_sunburst_html(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert "m-usage" in page and "m-inode" in page


def test_a_mount_name_cannot_break_out_of_the_embedded_json_script():
    # Arrange -- an adversarial mount name containing a closing tag.
    snap = _snap([_row("h", "/x</script>", 10, 50.0, 5.0)])

    # Act
    page = build_sunburst_html(snap)

    # Assert -- the slash is escaped, proving the guard fired.
    assert "<\\/script>" in page


def _dir_tree(tmp_path):
    """Two folders: big/ holds 100B across two files, small/ holds 10B."""
    big = tmp_path / "big"
    (big / "deep").mkdir(parents=True)
    (big / "deep" / "f.bin").write_bytes(b"x" * 60)
    (big / "top.bin").write_bytes(b"x" * 40)
    small = tmp_path / "small"
    small.mkdir()
    (small / "s.bin").write_bytes(b"x" * 10)
    return tmp_path


@pytest.mark.requires_fd
def test_dir_hierarchy_root_names_the_scanned_folder(tmp_path):
    # Arrange
    from scitex_storage._render import build_dir_hierarchy

    root = _dir_tree(tmp_path)
    # Act
    hierarchy = build_dir_hierarchy("Paper", root)
    # Assert
    assert (hierarchy["name"], hierarchy["value"]) == ("Paper", 110)


@pytest.mark.requires_fd
def test_dir_hierarchy_orders_largest_first_and_nests_folders(tmp_path):
    # Arrange
    from scitex_storage._render import build_dir_hierarchy

    root = _dir_tree(tmp_path)
    # Act
    hierarchy = build_dir_hierarchy("Paper", root)
    # Assert
    result = (
        [c["name"] for c in hierarchy["children"]],
        hierarchy["children"][0]["children"][0]["name"],
    )
    assert result == (["big", "small"], "deep")


@pytest.mark.requires_fd
def test_dir_hierarchy_rolls_up_beyond_top_n(tmp_path):
    # Arrange
    from scitex_storage._render import build_dir_hierarchy

    root = _dir_tree(tmp_path)
    # Act
    hierarchy = build_dir_hierarchy("Paper", root, top_n=1)
    # Assert
    result = (
        [c["name"] for c in hierarchy["children"]],
        hierarchy["children"][-1]["value"],
    )
    assert result == (["big", "Other"], 10)


def test_dir_hierarchy_needs_the_fd_binary(tmp_path):
    # Arrange -- an empty PATH hides fd and fdfind alike (sanctioned
    # save/set/restore env pattern: no monkeypatch anywhere in this repo).
    import os

    from scitex_storage._measure._scan import MissingSystemDependencyError
    from scitex_storage._render import build_dir_hierarchy

    previous_path = os.environ.get("PATH")
    (tmp_path / "sub").mkdir()
    os.environ["PATH"] = str(tmp_path)
    try:
        # Act
        raised = None
        try:
            build_dir_hierarchy("Paper", tmp_path)
        except MissingSystemDependencyError as exc:
            raised = exc
    finally:
        if previous_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = previous_path
    # Assert
    assert raised is not None


@pytest.mark.requires_fd
def test_dir_page_has_no_colour_mode_buttons(tmp_path):
    # Arrange -- folders carry no usage metric, so the fleet colour modes
    # would be dead controls here.
    from scitex_storage._render import build_dir_hierarchy, build_dir_sunburst_html

    hierarchy = build_dir_hierarchy("Paper", _dir_tree(tmp_path))
    # Act
    page = build_dir_sunburst_html("Paper", hierarchy, "gen")
    # Assert -- the JS guards reference the ids as strings; the dead
    # controls are the buttons themselves.
    assert ('<button id="m-usage"' in page, '<button id="m-inode"' in page) == (False, False)


@pytest.mark.requires_fd
def test_dir_page_title_and_footer_name_folders_not_hosts(tmp_path):
    # Arrange
    from scitex_storage._render import build_dir_hierarchy, build_dir_sunburst_html

    hierarchy = build_dir_hierarchy("Paper", _dir_tree(tmp_path))
    # Act
    page = build_dir_sunburst_html("Paper", hierarchy, "gen")
    # Assert
    assert ("Paper" in page, "click a folder to zoom" in page) == (True, True)


@pytest.mark.requires_fd
def test_a_folder_name_cannot_break_out_of_the_embedded_json_script():
    # Arrange -- an adversarial folder name containing a closing tag.
    # A single path component cannot hold "/" (it would nest), so the
    # hierarchy is built directly: the guard lives in the page builder.
    import json
    import re

    from scitex_storage._render import build_dir_sunburst_html

    evil = 'x</script><script>alert(1)</script>'
    hierarchy = {
        "name": "Paper",
        "value": 1,
        "usage": None,
        "inode": None,
        "children": [
            {"name": evil, "value": 1, "usage": None, "inode": None, "children": []}
        ],
    }
    # Act
    page = build_dir_sunburst_html("Paper", hierarchy, "gen")
    result = (
        json.loads(re.search(r"const ROOT = (\{.*?\});", page, re.S).group(1))["children"][0]["name"],
        "<\\/script>" in page,
    )
    # Assert -- the name round-trips exactly, and the slash is escaped,
    # proving the guard fired (the page's own </script> close tag aside).
    assert result == (evil, True)

# EOF

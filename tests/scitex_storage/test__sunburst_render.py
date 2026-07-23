"""Unit tests for scitex_storage._sunburst_render (Codecov-style sunburst).

Pure renderer over dataclasses -- no mocks, no I/O. The hierarchy shape
and the two colour metrics (space% and inode%) are the substance, plus
the self-contained / no-tag-breakout guarantees shared with the bubble
view.
"""

from __future__ import annotations

from scitex_storage._fleet_status import MEASURED, FleetSnapshot, HostStorage
from scitex_storage._sunburst_render import build_hierarchy, build_sunburst_html

GB = 1024**3


def _snap(rows):
    return FleetSnapshot(rows=rows, generated_at="t", note="n")


def _row(host, mount, size_gb, space, inode):
    return HostStorage(host=host, role="r", mount=mount, verdict=MEASURED,
                       size_bytes=size_gb * GB, used_pct=space, inode_used_pct=inode)


def test_hierarchy_root_is_the_fleet():
    # Act
    root = build_hierarchy(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert root["name"] == "Fleet"


def test_hosts_are_the_first_ring():
    # Act
    root = build_hierarchy(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert root["children"][0]["name"] == "h"


def test_filesystems_are_the_second_ring():
    # Act
    root = build_hierarchy(_snap([_row("h", "/data", 10, 50.0, 5.0)]))
    # Assert
    assert root["children"][0]["children"][0]["name"] == "/data"


def test_a_segments_value_is_its_capacity_for_the_angle():
    # Act
    root = build_hierarchy(_snap([_row("h", "/", 40, 50.0, 5.0)]))
    # Assert
    assert root["children"][0]["value"] == 40 * GB


def test_a_node_carries_both_colour_metrics():
    # The operator wants to switch colour between space and inodes.
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
    # Act
    page = build_sunburst_html(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert 'src="http' not in page and 'href="http' not in page


def test_both_colour_mode_buttons_are_present():
    # Act
    page = build_sunburst_html(_snap([_row("h", "/", 10, 50.0, 5.0)]))
    # Assert
    assert "m-usage" in page and "m-inode" in page


def test_a_mount_name_cannot_break_out_of_the_embedded_json_script():
    # Arrange
    page = build_sunburst_html(_snap([_row("h", "/x</script>", 10, 50.0, 5.0)]))
    # Assert -- the slash is escaped, proving the guard fired.
    assert "<\\/script>" in page

# EOF

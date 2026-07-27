"""Unit tests for scitex_storage._render (capacity-bubble view).

Pure renderer over dataclasses -- no mocks, no I/O. The aggregation rules
are the substance: a host's circle must encode only capacity the probe
actually established, and the page must be self-contained (no external
chart library) so it opens offline.
"""

from __future__ import annotations

from scitex_storage._render import aggregate_hosts, build_bubbles_html
from scitex_storage._fleet_status import (
    COULD_NOT_LOOK,
    MEASURED,
    NOT_APPLICABLE,
    FleetSnapshot,
    HostStorage,
)

GB = 1024**3


def _snap(rows):
    return FleetSnapshot(rows=rows, generated_at="t", note="n")


def test_a_hosts_capacity_is_the_sum_of_its_measured_filesystems():
    # Arrange
    rows = [
        HostStorage(host="h", role="r", mount="/a", verdict=MEASURED,
                    size_bytes=100 * GB, used_pct=50.0),
        HostStorage(host="h", role="r", mount="/b", verdict=MEASURED,
                    size_bytes=300 * GB, used_pct=10.0),
    ]
    # Act
    rec = aggregate_hosts(_snap(rows))[0]
    # Assert
    assert rec["total_bytes"] == 400 * GB


def test_host_usage_is_capacity_weighted_not_a_plain_average():
    # 50% of 100G + 10% of 300G = 80G used of 400G = 20%, not (50+10)/2.
    # Arrange
    rows = [
        HostStorage(host="h", role="r", mount="/a", verdict=MEASURED,
                    size_bytes=100 * GB, used_pct=50.0),
        HostStorage(host="h", role="r", mount="/b", verdict=MEASURED,
                    size_bytes=300 * GB, used_pct=10.0),
    ]
    # Act
    rec = aggregate_hosts(_snap(rows))[0]
    # Assert
    assert rec["used_pct"] == 20.0


def test_structural_filesystems_do_not_inflate_capacity():
    # A read-only squashfs image has a size but is not real capacity.
    # Arrange
    rows = [
        HostStorage(host="h", role="r", mount="/", verdict=MEASURED,
                    size_bytes=100 * GB, used_pct=40.0),
        HostStorage(host="h", role="r", mount="/snap/x", verdict=NOT_APPLICABLE,
                    size_bytes=1 * GB, used_pct=None),
    ]
    # Act
    rec = aggregate_hosts(_snap(rows))[0]
    # Assert
    assert rec["total_bytes"] == 100 * GB


def test_a_could_not_look_row_does_not_invent_usage():
    # Arrange
    rows = [
        HostStorage(host="h", role="r", mount="/x", verdict=COULD_NOT_LOOK,
                    size_bytes=None, used_pct=None),
    ]
    # Act
    rec = aggregate_hosts(_snap(rows))[0]
    # Assert -- present, but no fabricated usage.
    assert rec["used_pct"] is None


def test_a_host_with_no_readable_filesystem_still_appears():
    # It must not silently vanish from the capacity view.
    # Arrange
    rows = [
        HostStorage(host="h", role="r", mount="/x", verdict=COULD_NOT_LOOK,
                    size_bytes=None, used_pct=None),
    ]
    # Act
    recs = aggregate_hosts(_snap(rows))
    # Assert
    assert len(recs) == 1


def test_apfs_volumes_sharing_a_container_are_counted_once():
    # macOS: six APFS volumes on /dev/disk3 each report the container's
    # full 240G. Summing them read 2.7T for a 245G disk. Fold to one.
    # Arrange -- three volumes, same disk, same total+avail.
    rows = [
        HostStorage(host="mba", role="r", mount="/", verdict=MEASURED,
                    size_bytes=240 * GB, used_pct=25.0, avail_bytes=60 * GB,
                    source="/dev/disk3s1s1"),
        HostStorage(host="mba", role="r", mount="/System/Volumes/Data", verdict=MEASURED,
                    size_bytes=240 * GB, used_pct=70.0, avail_bytes=60 * GB,
                    source="/dev/disk3s5"),
        HostStorage(host="mba", role="r", mount="/System/Volumes/VM", verdict=MEASURED,
                    size_bytes=240 * GB, used_pct=3.0, avail_bytes=60 * GB,
                    source="/dev/disk3s6"),
    ]
    # Act
    rec = aggregate_hosts(_snap(rows))[0]
    # Assert -- one container of 240G, not 720G.
    assert rec["total_bytes"] == 240 * GB


def test_a_folded_container_usage_is_total_minus_available():
    # The honest container occupancy: 240G - 60G avail = 180G = 75%,
    # not any single volume's 25/70/3%.
    # Arrange
    rows = [
        HostStorage(host="mba", role="r", mount="/", verdict=MEASURED,
                    size_bytes=240 * GB, used_pct=25.0, avail_bytes=60 * GB,
                    source="/dev/disk3s1s1"),
        HostStorage(host="mba", role="r", mount="/System/Volumes/Data", verdict=MEASURED,
                    size_bytes=240 * GB, used_pct=70.0, avail_bytes=60 * GB,
                    source="/dev/disk3s5"),
    ]
    # Act
    rec = aggregate_hosts(_snap(rows))[0]
    # Assert
    assert rec["used_pct"] == 75.0


def test_distinct_linux_devices_do_not_fold():
    # Two real, separate filesystems on Linux must both count.
    # Arrange
    rows = [
        HostStorage(host="h", role="r", mount="/", verdict=MEASURED,
                    size_bytes=100 * GB, used_pct=50.0, avail_bytes=50 * GB,
                    source="/dev/sda1"),
        HostStorage(host="h", role="r", mount="/data", verdict=MEASURED,
                    size_bytes=200 * GB, used_pct=50.0, avail_bytes=100 * GB,
                    source="/dev/sdb1"),
    ]
    # Act
    rec = aggregate_hosts(_snap(rows))[0]
    # Assert
    assert rec["total_bytes"] == 300 * GB


def test_hosts_are_ordered_largest_capacity_first():
    # Arrange
    rows = [
        HostStorage(host="small", role="r", mount="/", verdict=MEASURED,
                    size_bytes=10 * GB, used_pct=5.0),
        HostStorage(host="big", role="r", mount="/", verdict=MEASURED,
                    size_bytes=900 * GB, used_pct=5.0),
    ]
    # Act
    recs = aggregate_hosts(_snap(rows))
    # Assert
    assert recs[0]["host"] == "big"


def test_the_page_embeds_no_external_chart_library():
    # Self-contained: opens offline, like the table dashboard.
    # Arrange
    rows = [HostStorage(host="h", role="r", mount="/", verdict=MEASURED,
                        size_bytes=GB, used_pct=50.0)]
    # Act
    page = build_bubbles_html(_snap(rows))
    # Assert
    assert "http://" not in page and "https://" not in page


def test_a_mount_name_cannot_break_out_of_the_embedded_json_script():
    # An adversarial mount containing </script> must be escaped so it
    # cannot close the embedding <script> tag early.
    # Arrange
    rows = [HostStorage(host="h", role="r", mount="/x</script>", verdict=MEASURED,
                        size_bytes=GB, used_pct=50.0)]
    # Act
    page = build_bubbles_html(_snap(rows))
    # Assert -- the mount's slash is escaped (<\/script>), proving the guard fired.
    assert "<\\/script>" in page

# EOF

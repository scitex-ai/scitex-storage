"""Unit tests for scitex_storage._transfer._scrub (cold re-verification).

Same fake-runner seam as the archive tests: no network, no SSH config.
The scrub reads a MANIFEST (not a live source tree -- the source is
usually gone, which is the entire point) and re-measures the destination.
"""

from scitex_storage._transfer._archive import ArchiveManifest
from scitex_storage._transfer._scrub import scrub_manifest


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []


def _manifest(tmp_path, **overrides):
    fields = dict(
        source=str(tmp_path / "gone"),
        destination="scitex-nas-02",
        remote_path="~/scitex-storage-archive/gone",
        size_bytes=5,
        file_count=1,
        checksummed=True,
        archived_at=0.0,
        verified="verified",
        verification_method="tally",
    )
    fields.update(overrides)
    return ArchiveManifest(**fields)


def test_scrub_verifies_unchanged_destination(tmp_path):
    # Arrange
    manifest = _manifest(tmp_path)

    def faithful(cmd, **kwargs):
        joined = " ".join(str(p) for p in cmd)
        if "find " in joined and "sha256sum" in joined:
            return _FakeCompletedProcess(0, "", "")
        return _FakeCompletedProcess(0, "1\n5\n", "")

    # Act
    report = scrub_manifest(manifest, runner=faithful)
    # Assert
    assert report.tally_verdict == "verified"


def test_scrub_reports_mismatch_on_drifted_destination(tmp_path):
    # Arrange
    manifest = _manifest(tmp_path)

    def drifted(cmd, **kwargs):
        return _FakeCompletedProcess(0, "0\n0\n", "")

    # Act
    report = scrub_manifest(manifest, runner=drifted)
    # Assert
    assert report.tally_verdict == "mismatch"


def test_scrub_reports_could_not_look_when_probe_fails(tmp_path):
    # Arrange
    manifest = _manifest(tmp_path)

    def broken(cmd, **kwargs):
        return _FakeCompletedProcess(255, "", "ssh: connect refused")

    # Act
    report = scrub_manifest(manifest, runner=broken)
    # Assert
    assert report.tally_verdict == "could-not-look"


def test_scrub_content_unknown_without_baseline(tmp_path):
    # Arrange -- tally-cleared manifests carry no content_digest.
    manifest = _manifest(tmp_path)

    def faithful(cmd, **kwargs):
        return _FakeCompletedProcess(0, "1\n5\n", "")

    # Act
    report = scrub_manifest(manifest, content=True, runner=faithful)
    # Assert
    assert report.content_verdict == "unknown"

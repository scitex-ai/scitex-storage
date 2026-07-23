"""Unit tests for the fleet dashboard view and its snapshot cache.

The view must NEVER gather live (that ssh-probes six hosts, ~90s, and
would hang the request). It reads a cached render written out of band.
These tests exercise both states -- snapshot present and snapshot absent
-- with a REAL cached file on disk, no mocks.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("django")


def _configure_django():
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            ROOT_URLCONF="scitex_storage._django.urls",
            ALLOWED_HOSTS=["*"],
            DATABASES={},
            INSTALLED_APPS=[],
        )
        django.setup()


def test_absent_snapshot_returns_a_placeholder_not_an_error(tmp_path, monkeypatch):
    # "not gathered yet" is a real first-run state, not a 500.
    # Arrange
    _configure_django()
    from django.test import RequestFactory

    from scitex_storage._django import views

    monkeypatch.setenv("SCITEX_DIR", str(tmp_path))  # empty -> no snapshot
    request = RequestFactory().get("/fleet")

    # Act
    response = views.fleet(request)

    # Assert
    assert response.status_code == 200


def test_absent_snapshot_names_the_command_to_fix_it(tmp_path, monkeypatch):
    # Arrange
    _configure_django()
    from django.test import RequestFactory

    from scitex_storage._django import views

    monkeypatch.setenv("SCITEX_DIR", str(tmp_path))
    request = RequestFactory().get("/fleet")

    # Act
    body = views.fleet(request).content.decode()

    # Assert
    assert "fleet-status" in body


def test_a_present_snapshot_is_served_verbatim(tmp_path, monkeypatch):
    # Arrange
    _configure_django()
    from django.test import RequestFactory

    from scitex_storage._django import views
    from scitex_storage._observe import default_snapshot_path

    monkeypatch.setenv("SCITEX_DIR", str(tmp_path))
    snap_path = default_snapshot_path()
    os.makedirs(os.path.dirname(snap_path), exist_ok=True)
    with open(snap_path, "w", encoding="utf-8") as fh:
        fh.write("<html><body>FLEET-SENTINEL</body></html>")
    request = RequestFactory().get("/fleet")

    # Act
    body = views.fleet(request).content.decode()

    # Assert
    assert "FLEET-SENTINEL" in body

# EOF

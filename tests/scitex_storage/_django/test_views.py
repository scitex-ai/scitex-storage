"""Unit tests for scitex_storage._django.views.

Guarded (`pytest.importorskip`) -- meaningful only once Django + scitex-app
+ scitex-ui are installed (the `gui` extra). Exercises the view against
scitex-storage's REAL `scan()` on a real temp directory -- the "real
data, not a placeholder" proof this scaffold exists to demonstrate.
"""

from __future__ import annotations

import os

import pytest


def _boot_django_for_storage_gui():
    """Shared helper -- NOT a test, does not itself need AAA markers."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")
    import django

    django.setup()


def _touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_index_renders_200_for_a_real_directory(tmp_path):
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()
    _touch(tmp_path / "child" / "a.bin", 100)
    from django.test import RequestFactory

    from scitex_storage._django.views import index

    # Act
    response = index(RequestFactory().get("/storage/", {"path": str(tmp_path)}))
    # Assert
    assert response.status_code == 200


def test_index_renders_the_real_directory_name_from_a_real_scan(tmp_path):
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()
    _touch(tmp_path / "alpha" / "a.bin", 100)
    from django.test import RequestFactory

    from scitex_storage._django.views import index

    # Act
    body = index(RequestFactory().get("/storage/", {"path": str(tmp_path)})).content.decode()
    # Assert -- the child name from the REAL directory tree we built above
    # appears in the rendered HTML, not a placeholder.
    assert "alpha" in body


def test_index_reports_a_friendly_error_for_a_missing_path(tmp_path):
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()
    missing = tmp_path / "does-not-exist"
    from django.test import RequestFactory

    from scitex_storage._django.views import index

    # Act
    body = index(RequestFactory().get("/storage/", {"path": str(missing)})).content.decode()
    # Assert -- never a bare 500 / raw traceback for a browser-facing page.
    assert "does not exist" in body


def test_healthz_returns_ok():
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()
    from django.test import RequestFactory

    from scitex_storage._django.views import healthz

    # Act
    body = healthz(RequestFactory().get("/storage/healthz")).content
    # Assert
    assert body == b"ok"


# EOF

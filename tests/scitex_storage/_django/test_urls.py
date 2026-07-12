"""Unit tests for scitex_storage._django.urls.

Guarded (`pytest.importorskip`) -- meaningful only once Django is
installed (the `gui` extra). Pins the URL contract hub mounts against:
`app_name = "scitex_storage"` and an `index` route at the app root.
"""

from __future__ import annotations

import pytest


def test_app_name_is_scitex_storage():
    # Arrange
    pytest.importorskip("django")
    from scitex_storage._django import urls

    # Act
    app_name = urls.app_name
    # Assert -- hub's `include("scitex_storage._django.urls")` relies on
    # this namespace to avoid colliding with another app's route names.
    assert app_name == "scitex_storage"


def test_index_route_is_registered():
    # Arrange
    pytest.importorskip("django")
    from scitex_storage._django import urls

    # Act
    names = {p.name for p in urls.urlpatterns}
    # Assert
    assert "index" in names


def test_healthz_route_is_registered():
    # Arrange
    pytest.importorskip("django")
    from scitex_storage._django import urls

    # Act
    names = {p.name for p in urls.urlpatterns}
    # Assert
    assert "healthz" in names


# EOF

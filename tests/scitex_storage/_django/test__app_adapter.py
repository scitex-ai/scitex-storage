"""Test the real public SDK App embedding seam when GUI dependencies exist.

Root-scoped skips distinguish missing optional packages from a broken public
embedding API. The adapter must re-export the SDK's actual AppConfig class.
"""
from __future__ import annotations

import importlib

import pytest

pytest.importorskip("django")


def test_adapter_reexports_the_real_scitex_app_config_when_installed():
    # Arrange
    pytest.importorskip("scitex_sdk")
    importlib.import_module("scitex_sdk.app.embed")
    from scitex_sdk.app.embed import ScitexAppConfig as RealScitexAppConfig

    from scitex_storage._django._app_adapter import ScitexAppConfig

    # Act
    resolved = ScitexAppConfig
    # Assert
    assert resolved is RealScitexAppConfig


def test_adapter_config_is_a_real_django_class():
    # Arrange
    from django.apps import AppConfig
    from scitex_storage._django._app_adapter import ScitexAppConfig

    # Act
    is_django_config = issubclass(ScitexAppConfig, AppConfig)

    # Assert
    assert is_django_config is True


def test_run_standalone_is_callable():
    # Arrange
    from scitex_storage._django._app_adapter import run_standalone

    # Act
    is_callable = callable(run_standalone)
    # Assert
    assert is_callable is True


# EOF

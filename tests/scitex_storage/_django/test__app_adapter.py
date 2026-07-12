"""Unit tests for scitex_storage._django._app_adapter.

The adapter is the ONE place scitex-storage is allowed to import
scitex-app's private `_django`/`_standalone` modules (see that module's
docstring). Guarded (`pytest.importorskip`) -- meaningful only once
Django + scitex-app are installed (the `gui` extra); the whole
`scitex_storage._django` package, including the adapter's own
`except ImportError` fallback branch, requires Django to import at all.

No test exercises the "scitex-app absent" fallback branch directly --
doing so honestly (without `monkeypatch`, banned by this repo's no-mocks
rule) would require either an isolated subprocess with scitex-app
uninstalled, or hiding a real installed package from `sys.modules`,
neither of which is worth the complexity for a 2-line
`try/except ImportError` that mirrors scitex-writer's own real, shipped
`apps.py` verbatim.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")


def test_adapter_reexports_the_real_scitex_app_config_when_installed():
    # Arrange
    pytest.importorskip("scitex_app._django")
    from scitex_app._django import ScitexAppConfig as RealScitexAppConfig

    from scitex_storage._django._app_adapter import ScitexAppConfig

    # Act
    resolved = ScitexAppConfig
    # Assert
    assert resolved is RealScitexAppConfig


def test_run_standalone_is_callable():
    # Arrange
    from scitex_storage._django._app_adapter import run_standalone

    # Act
    is_callable = callable(run_standalone)
    # Assert
    assert is_callable is True


# EOF

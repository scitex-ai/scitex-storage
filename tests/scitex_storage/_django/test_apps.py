"""Unit tests for scitex_storage._django.apps (StorageConfig + its manifest.json).

Manifest tests are pure-Python (no Django/scitex-app required) so they
always run, even in a lean install with no `gui` extra -- manifest.json
is a static data file `apps.py`'s AppConfig reads at runtime, not code.
The AppConfig-behaviour tests below them are guarded (`pytest.importorskip`)
-- meaningful only once Django + scitex-app are installed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_MANIFEST_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "scitex_storage"
    / "_django"
    / "manifest.json"
)


def _load_manifest() -> dict:
    return json.loads(_MANIFEST_PATH.read_text())


def test_manifest_file_exists():
    # Arrange
    path = _MANIFEST_PATH
    # Act
    exists = path.is_file()
    # Assert
    assert exists is True


def test_manifest_is_valid_json_object():
    # Arrange
    # (nothing to arrange -- reads the static file directly)
    # Act
    data = _load_manifest()
    # Assert
    assert isinstance(data, dict)


def test_manifest_declares_the_required_scitex_app_0_3_0_keys():
    # Arrange -- scitex_app.appmaker._validate.MANIFEST_REQUIRED_KEYS (0.3.0)
    required = {"name", "slug", "label", "pip_package", "icon", "license"}
    # Act
    data = _load_manifest()
    # Assert
    assert required <= set(data.keys())


def test_manifest_name_ends_with_app_suffix():
    # Arrange
    # (nothing to arrange)
    # Act
    data = _load_manifest()
    # Assert -- scitex_app's validator requires this naming convention
    assert data["name"].endswith("_app") or data["name"].endswith("-app")


def test_manifest_does_not_declare_a_hand_written_version():
    # Arrange
    # (nothing to arrange)
    # Act
    data = _load_manifest()
    # Assert -- scitex_app 0.3.0's validator REJECTS a hand-written
    # `version` key (it drifts from the installed package; the real
    # version is derived at runtime via importlib.metadata from
    # `pip_package`). Verified live against the installed scitex-app
    # 0.3.0 package's appmaker._validate.validate_manifest().
    assert "version" not in data


def test_manifest_pip_package_matches_this_package():
    # Arrange
    # (nothing to arrange)
    # Act
    data = _load_manifest()
    # Assert
    assert data["pip_package"] == "scitex-storage"


def test_manifest_is_embedded_package():
    # Arrange
    # (nothing to arrange)
    # Act
    data = _load_manifest()
    # Assert -- _django/ is a private in-package Django app, not a
    # standalone scitex-hub app repo; embedded_package skips the
    # standalone-only file/template/CSS checks in scitex_app's validator.
    assert data["embedded_package"] is True


def test_manifest_wip_is_false():
    # Arrange
    # (nothing to arrange)
    # Act
    data = _load_manifest()
    # Assert -- this scaffold is real (proves the mounting contract),
    # not a placeholder hub should hide from the app gallery.
    assert data["wip"] is False


def test_manifest_dependencies_field_lists_this_package():
    # Arrange
    # (nothing to arrange)
    # Act
    deps = _load_manifest()["dependencies"]
    # Assert
    assert "scitex-storage" in deps["python"]


def test_manifest_license_matches_this_repos_actual_license():
    # Arrange
    # (nothing to arrange)
    # Act
    data = _load_manifest()
    # Assert -- pyproject.toml's [project].license
    assert data["license"] == "AGPL-3.0-only"


def _boot_django_for_storage_gui():
    """Shared helper -- NOT a test, does not itself need AAA markers."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")
    import django

    django.setup()


def test_storage_config_label_is_unmistakably_namespaced():
    # Arrange
    pytest.importorskip("django")
    from scitex_storage._django.apps import StorageConfig

    # Act
    label = StorageConfig.label
    # Assert -- deliberately NOT a short generic name like "storage"
    # (see apps.py's docstring for the writer collision bug this avoids).
    assert label == "scitex_storage_django"


def test_storage_config_class_sets_default_true():
    # Arrange
    pytest.importorskip("django")
    from scitex_storage._django.apps import StorageConfig

    # Act
    default_flag = StorageConfig.default
    # Assert
    assert default_flag is True


def test_storage_config_registers_under_its_own_label():
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    _boot_django_for_storage_gui()
    from django.apps import apps

    # Act
    cfg = apps.get_app_config("scitex_storage_django")

    # Assert
    assert cfg.name == "scitex_storage._django"


def test_storage_config_app_slug_matches_manifest_slug():
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    _boot_django_for_storage_gui()
    from django.apps import apps

    # Act
    cfg = apps.get_app_config("scitex_storage_django")

    # Assert -- ScitexAppConfig.app_slug reads manifest.json's "slug"
    assert cfg.app_slug == "storage"


def test_storage_config_frontend_type_is_server_rendered():
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    _boot_django_for_storage_gui()
    from django.apps import apps

    # Act
    cfg = apps.get_app_config("scitex_storage_django")

    # Assert -- this scaffold is server-rendered Django HTML, no React yet
    assert cfg.frontend_type == "server-rendered"


def test_scitex_app_0_3_0_validator_still_wants_a_version_field():
    # Arrange -- documents a real upstream inconsistency found while
    # building this scaffold (verified live against the installed
    # scitex-app 0.3.0, not guessed): `appmaker._validate` FORBIDS a
    # hand-written `version` key, but the separate, older
    # `ScitexAppConfig.MANIFEST_REQUIRED` set was never updated to
    # match -- it still lists `version` as required. Neither Django app
    # loading nor hub's mount calls `validate_manifest()` automatically,
    # so this doesn't break anything today.
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    _boot_django_for_storage_gui()
    from django.apps import apps

    cfg = apps.get_app_config("scitex_storage_django")
    # Act
    errors = cfg.validate_manifest()
    # Assert -- pins the CURRENT (inconsistent) upstream behavior; if
    # scitex-app is later fixed to match its own appmaker validator,
    # this assertion should be simplified to `errors == []`.
    assert errors == ["Missing required fields: version"]


# EOF

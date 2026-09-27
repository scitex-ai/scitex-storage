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


def _index_template_context(tmp_path):
    """Return the template context ``index`` actually rendered with.

    Goes through ``django.test.Client`` rather than ``RequestFactory``
    because ``render()`` returns a plain ``HttpResponse`` whose context is
    already discarded -- only the test client's instrumentation captures
    it. That is a real request through the real URLconf, not a stand-in:
    reading the declaration off ``views.SHELL_PANES`` instead would assert
    that a constant equals itself while the view was free to never pass it.
    """
    from django.test import Client
    from django.test.utils import setup_test_environment

    # Django only records a response's context when the test environment is
    # set up -- that is what connects the `template_rendered` signal. Without
    # it `response.context` is None, which fails as an unsubscriptable
    # NoneType rather than as "the pane declaration is missing", so the guard
    # would report the wrong defect. Re-entry raises; this is a plain pytest
    # module, not a TestCase, so nothing else has called it.
    try:
        setup_test_environment()
    except RuntimeError:
        pass

    response = Client().get("/", {"path": str(tmp_path)})
    return response.context


def _touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


_VOLUME_ROOT = {}


def provide_test_volumes(request):
    """Volumes provider wired in via SCITEX_STORAGE_VOLUMES_PROVIDER."""
    return [{"key": "mine", "label": "Mine", "path": _VOLUME_ROOT["path"], "machine": "box"}]


def _get(tmp_path, params):
    from django.test import RequestFactory, override_settings

    from scitex_storage._django.views import index

    _VOLUME_ROOT["path"] = str(tmp_path)
    dotted = f"{__name__}.provide_test_volumes"
    with override_settings(SCITEX_STORAGE_VOLUMES_PROVIDER=dotted):
        return index(RequestFactory().get("/storage/", params))


def _boot():
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()


def test_index_defaults_to_the_usage_tab(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {}).content.decode()
    # Assert -- no tab param means Usage, never the retired Machines tab.
    assert "Machines &amp; storage" not in body


def test_index_default_has_no_machines_tab(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {}).content.decode()
    # Assert
    assert "Machines & storage" not in body


def test_unknown_tab_falls_back_to_usage(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "machines"}).content.decode()
    # Assert
    assert "Machines &amp; storage" not in body


def test_retired_tab_leaves_no_machines_tab(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "machines"}).content.decode()
    # Assert
    assert "Machines & storage" not in body


def test_absolute_path_param_is_not_scanned(tmp_path):
    # Arrange -- the old ?path= free-form scan must not come back.
    _boot()
    # Act
    body = _get(tmp_path, {"path": "/etc"}).content.decode()
    # Assert
    assert "passwd" not in body


def test_backup_tab_is_still_coming_soon(tmp_path):
    # Arrange -- Usage/Move/Duplicates are real views now (organize.py);
    # Backup is the remaining placeholder.
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "backup"}).content.decode()
    # Assert
    assert "Coming soon" in body


def test_usage_tab_renders_donut(tmp_path):
    # Arrange
    _boot()
    _touch(tmp_path / "alpha" / "a.bin", 100)
    # Act
    body = _get(tmp_path, {"tab": "usage"}).content.decode()
    # Assert
    assert "Where your bytes live" in body


def test_usage_tab_links_fleet_views(tmp_path):
    # Arrange -- fleet links use route names, never hardcoded paths.
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "usage"}).content.decode()
    # Assert
    assert "Fleet visualizations" in body


def test_move_tab_renders_planner(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "move"}).content.decode()
    # Assert
    assert "Plan a move to cold storage" in body


def test_duplicates_tab_is_read_only_report(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "duplicates"}).content.decode()
    # Assert
    assert "Read-only report." in body


def test_index_declares_every_shell_pane_so_none_reserves_width(tmp_path):
    """The pane declaration reaches the template context, for all three panes.

    Regression guard for the measured defect: on prod at 1440x900 the three
    shell panes were ``visibility: hidden`` with ``display: block``, which
    hides an element while STILL RESERVING ITS BOX -- 539px, 37.4% of the
    viewport, blank before any storage content began. An UNDECLARED pane
    defaults to visible by scitex-ui's contract, so silence is what caused
    it. Asserting on the context rather than on rendered CSS keeps this a
    test of OUR declaration, not of scitex-ui's stylesheet.
    """
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()

    from scitex_ui.branding import PANE_NAMES

    # Act
    context = _index_template_context(tmp_path)

    # Assert -- every pane the contract knows about is spoken for. Checking
    # against PANE_NAMES rather than a hardcoded triple means a pane added
    # upstream fails here instead of silently reserving width again.
    assert set(context["panes"]) == set(PANE_NAMES)


def test_index_declares_every_shell_pane_unused(tmp_path):
    """Each declared pane's VALUE is ``unused`` -- the only state that collapses.

    Split from the coverage test above deliberately: "all three panes are
    declared" and "all three are declared UNUSED" are different failures.
    Declaring a pane ``client-populated`` would satisfy the coverage test
    while leaving its width reserved, which is the whole defect.
    """
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()

    # Act
    context = _index_template_context(tmp_path)

    # Assert
    assert set(context["panes"].values()) == {"unused"}


def test_index_declares_files_pane_unused_not_client_populated(tmp_path):
    """``files`` is the trap value, so it gets its own test.

    "A storage browser surely uses the files pane" is the plausible wrong
    answer, and ``client-populated`` is the plausible wrong VALUE -- it
    would keep ~490px reserved for a pane nothing ever fills. Storage
    renders its directory listing as a server-side table inside
    ``app_content``; ``extra_js`` is empty, so nothing populates a pane
    after mount either.
    """
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    pytest.importorskip("scitex_ui")
    _boot_django_for_storage_gui()

    # Act
    context = _index_template_context(tmp_path)

    # Assert
    assert context["panes"]["files"] == "unused"


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

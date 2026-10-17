"""Unit tests for scitex_storage._django.views.

Guarded (`pytest.importorskip`) -- meaningful only once Django + scitex-app
+ scitex-ui are installed (the `gui` extra). Exercises the view against
scitex-storage's REAL `scan()` on a real temp directory -- the "real
data, not a placeholder" proof this scaffold exists to demonstrate.
"""

from __future__ import annotations

import importlib
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
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.app._django')
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.ui')
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


def test_usage_tab_links_fleet_views(scitex_dir):
    # Arrange -- operator 2026-10-17: no empty promo. With no snapshot
    # gathered the Usage tab shows no Fleet-visualizations section
    # (backend pipeline stays). Fleet links use route names, never
    # hardcoded paths.
    _boot()
    # Act
    body = _get(scitex_dir, {"tab": "usage"}).content.decode()
    # Assert
    assert "Fleet visualizations" not in body


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
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.app._django')
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.ui')
    _boot_django_for_storage_gui()

    from scitex_sdk.ui.branding import PANE_NAMES

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
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.app._django')
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.ui')
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
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.app._django')
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.ui')
    _boot_django_for_storage_gui()

    # Act
    context = _index_template_context(tmp_path)

    # Assert
    assert context["panes"]["files"] == "unused"


def test_healthz_returns_ok():
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.app._django')
    pytest.importorskip("scitex_sdk")
    importlib.import_module('scitex_sdk.ui')
    _boot_django_for_storage_gui()
    from django.test import RequestFactory

    from scitex_storage._django.views import healthz

    # Act
    body = healthz(RequestFactory().get("/storage/healthz")).content
    # Assert
    assert body == b"ok"


# --------------------------------------------------------------------------
# Fleet views on real snapshots: table / bubbles / sunburst serve the
# gathered pages verbatim, and name the gather command when absent.
# --------------------------------------------------------------------------


def _sandbox(tmp_path):
    """Point SCITEX_DIR at tmp_path; returns a restore callable.

    A real env-var save/set (not a mock): the code under test still calls
    the real default_*_path resolvers. Every test below sets its own value
    in Arrange, so no test reads ambient user state.
    """
    old = os.environ.get("SCITEX_DIR")
    os.environ["SCITEX_DIR"] = str(tmp_path)

    def restore():
        if old is None:
            os.environ.pop("SCITEX_DIR", None)
        else:
            os.environ["SCITEX_DIR"] = old

    return restore


@pytest.fixture
def scitex_dir(tmp_path):
    """Sandbox SCITEX_DIR at tmp_path for the test; restores on teardown.

    The sanctioned env-var pattern (real save/set, never a mock): the
    code under test still calls the real default_*_path resolvers.
    """
    restore = _sandbox(tmp_path)
    try:
        yield tmp_path
    finally:
        restore()


def _write_snapshot_file(path_str):
    """Write a sentinel snapshot page at the explicit path (no env involved)."""
    import os as _os

    _os.makedirs(_os.path.dirname(path_str), exist_ok=True)
    with open(path_str, "w", encoding="utf-8") as fh:
        fh.write("<html><body>FLEET-SENTINEL</body></html>")


def test_fleet_view_without_snapshot_names_the_gather_command(scitex_dir):
    # Arrange -- nothing gathered yet in this sandbox.
    _boot()
    from django.test import RequestFactory

    from scitex_storage._django.views import fleet

    # Act
    body = fleet(RequestFactory().get("/storage/fleet/")).content.decode()
    # Assert
    assert "fleet-status --gather" in body


def test_fleet_view_serves_a_gathered_snapshot(scitex_dir):
    # Arrange
    _boot()
    from scitex_storage._observe import default_snapshot_path

    _write_snapshot_file(default_snapshot_path())
    from django.test import RequestFactory

    from scitex_storage._django.views import fleet

    # Act
    body = fleet(RequestFactory().get("/storage/fleet/")).content.decode()
    # Assert
    assert "FLEET-SENTINEL" in body


def test_bubbles_view_without_snapshot_names_the_gather_command(scitex_dir):
    # Arrange
    _boot()
    from django.test import RequestFactory

    from scitex_storage._django.views import bubbles

    # Act
    body = bubbles(RequestFactory().get("/storage/bubbles/")).content.decode()
    # Assert
    assert "fleet-status --gather" in body


def test_bubbles_view_serves_a_gathered_snapshot(scitex_dir):
    # Arrange
    _boot()
    from scitex_storage._observe import default_bubbles_path

    _write_snapshot_file(default_bubbles_path())
    from django.test import RequestFactory

    from scitex_storage._django.views import bubbles

    # Act
    body = bubbles(RequestFactory().get("/storage/bubbles/")).content.decode()
    # Assert
    assert "FLEET-SENTINEL" in body


def test_sunburst_view_without_snapshot_names_the_gather_command(scitex_dir):
    # Arrange
    _boot()
    from django.test import RequestFactory

    from scitex_storage._django.views import sunburst

    # Act
    body = sunburst(RequestFactory().get("/storage/sunburst/")).content.decode()
    # Assert
    assert "fleet-status --gather" in body


def test_sunburst_view_serves_a_gathered_snapshot(scitex_dir):
    # Arrange
    _boot()
    from scitex_storage._observe import default_sunburst_path

    _write_snapshot_file(default_sunburst_path())
    from django.test import RequestFactory

    from scitex_storage._django.views import sunburst

    # Act
    body = sunburst(RequestFactory().get("/storage/sunburst/")).content.decode()
    # Assert
    assert "FLEET-SENTINEL" in body


def test_usage_tab_shows_fleet_links_once_a_snapshot_exists(scitex_dir):
    # Arrange -- a gathered snapshot turns the promo back on.
    _boot()
    from scitex_storage._observe import default_snapshot_path

    _write_snapshot_file(default_snapshot_path())
    # Act
    body = _get(scitex_dir, {"tab": "usage"}).content.decode()
    # Assert
    assert "Fleet visualizations" in body


def test_storage_pages_render_shared_app_header_row(tmp_path):
    """Both Storage surfaces carry the SDK SSoT header row (title slot).

    Operator header mandate (Writer/Scholar shape): the shared
    ``.stx-app-header`` row replaces the leaf-owned title block, on the
    Backup placeholder (index.html) AND the organize tabs (organize.html).
    """
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("stx-app-header" in body for body in bodies)


def test_storage_header_shows_live_leaf_version(tmp_path):
    """The shared row shows the LEAF version, never a hand-written string."""
    # Arrange
    _boot()
    from scitex_storage import __version__
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all(f"v{__version__}" in body for body in bodies)


def test_storage_header_carries_project_selector_slot(tmp_path):
    """The canonical project-selector slot is present (empty: user-scope)."""
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("stx-app-header__slot--project-selector" in body for body in bodies)


def test_storage_pages_carry_no_bespoke_title_markup(tmp_path):
    """The old leaf-owned h1.stx-storage-title block is gone everywhere."""
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("stx-storage-title" not in body for body in bodies)


# EOF

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
    return [
        {"key": "mine", "label": "Mine", "path": _VOLUME_ROOT["path"], "machine": "box"}
    ]


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
    importlib.import_module("scitex_sdk.app._django")
    pytest.importorskip("scitex_sdk")
    importlib.import_module("scitex_sdk.ui")
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
    importlib.import_module("scitex_sdk.app._django")
    pytest.importorskip("scitex_sdk")
    importlib.import_module("scitex_sdk.ui")
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
    importlib.import_module("scitex_sdk.app._django")
    pytest.importorskip("scitex_sdk")
    importlib.import_module("scitex_sdk.ui")
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
    importlib.import_module("scitex_sdk.app._django")
    pytest.importorskip("scitex_sdk")
    importlib.import_module("scitex_sdk.ui")
    _boot_django_for_storage_gui()

    # Act
    context = _index_template_context(tmp_path)

    # Assert
    assert context["panes"]["files"] == "unused"


def test_healthz_returns_ok():
    # Arrange
    pytest.importorskip("django")
    pytest.importorskip("scitex_sdk")
    importlib.import_module("scitex_sdk.app._django")
    pytest.importorskip("scitex_sdk")
    importlib.import_module("scitex_sdk.ui")
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
    """Both Storage surfaces mount the SDK AppHeader primitive (CCT4618).

    Operator SSoT: the header row must match all apps exactly — one row,
    only the app name differs. The row itself is SDK-owned
    (ts/app/app-header renders it client-side); what the server must emit
    is the primitive's mount root, so that is what is pinned here.
    """
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("data-stx-app-header" in body for body in bodies)


def test_storage_header_mount_carries_leaf_title(tmp_path):
    """The mount root stamps the translated leaf title.

    The "v" prefix and the hide-when-unknown rule are SDK-owned (covered by
    the SDK's own contract tests); the leaf's job is passing the right
    values in, never a hand-written string and never the host's version.
    """
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all('data-app-title="Storage"' in body for body in bodies)


def test_storage_header_mount_carries_leaf_version(tmp_path):
    """The mount root stamps the LEAF package version (never the host's)."""
    # Arrange
    _boot()
    from scitex_storage import __version__

    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all(f'data-app-version="{__version__}"' in body for body in bodies)


def test_storage_header_loads_sdk_header_css(tmp_path):
    """Both pages load the SDK header CSS (the row's 40px shape lives there)."""
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("scitex_sdk/ui/css/app/app-header.css" in body for body in bodies)


def test_storage_header_loads_sdk_header_module(tmp_path):
    """Both pages load the prebuilt auto-mount module.

    The primitive is code, not just classes: without app-header.js nothing
    mounts the [data-stx-app-header] root and the page ships a header mount
    that never renders.
    """
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("scitex_sdk/ui/js/app/app-header.js" in body for body in bodies)


def test_storage_pages_carry_no_bespoke_title_class(tmp_path):
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


def test_storage_pages_carry_no_handwritten_header_row(tmp_path):
    """No hand-written <header class="stx-app-header..."> anywhere.

    The row is SDK-owned and client-rendered; a server-rendered copy would
    double-mount (or drift from) the primitive.
    """
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all('<header class="stx-app-header' not in body for body in bodies)


def test_storage_pages_carry_no_leaf_row_class(tmp_path):
    """No leaf-local row class: the only Storage string on the row is title."""
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("storage-app-header" not in body for body in bodies)


def _both_surfaces_with_provider(tmp_path, provider_url="/stub-projects/"):
    """Render Backup (index) + Usage (organize) with a host provider URL set."""
    from django.test import override_settings

    with override_settings(SCITEX_PROJECT_PROVIDER_URL=provider_url):
        return (
            _get(tmp_path, {"tab": "backup"}).content.decode(),
            _get(tmp_path, {"tab": "usage"}).content.decode(),
        )


def test_storage_header_renders_picker_element_with_provider(tmp_path):
    """Both surfaces carry the picker element the mover drops in the slot.

    Operator CCT4707: the content dump showed an empty picker slot because
    NOTHING server-rendered a picker at all. With the host provider URL set
    (the hub sets ``SCITEX_PROJECT_PROVIDER_URL``; standalone leaves it
    empty) both surfaces must carry the ``data-stx-project-picker`` element
    — the same element Writer serves.
    """
    # Arrange
    _boot()
    # Act
    bodies = _both_surfaces_with_provider(tmp_path)
    # Assert
    assert all("data-stx-project-picker" in body for body in bodies)


def test_storage_header_picker_points_at_host_provider(tmp_path):
    """The rendered picker fetches from the host's provider URL, not a guess.

    The tag resolves ``settings.SCITEX_PROJECT_PROVIDER_URL`` (a literal
    path passes through; a URL name reverses) and stamps it as
    ``data-provider-url`` for the picker's HTTP transport.
    """
    # Arrange
    _boot()
    # Act
    bodies = _both_surfaces_with_provider(tmp_path, "/stub-projects/")
    # Assert
    assert all('data-provider-url="/stub-projects/"' in body for body in bodies)


def test_storage_header_includes_picker_mover_with_provider(tmp_path):
    """Both surfaces include the leaf mover that slots the picker (CCT4707).

    The SDK AppHeader primitive only ever CREATES the canonical slot — the
    leaf-owned ``header-picker.js`` moves the server-rendered picker into it
    after mount (module order after both auto-mounts, fail-soft otherwise).
    """
    # Arrange
    _boot()
    # Act
    bodies = _both_surfaces_with_provider(tmp_path)
    # Assert
    assert all("scitex_storage/header-picker.js" in body for body in bodies)


def test_storage_header_renders_no_picker_markup_without_provider(tmp_path):
    """Standalone (no host provider) ships no picker markup.

    The tag yields ``""`` without ``SCITEX_PROJECT_PROVIDER_URL``, so the
    content dump stays exactly as before the picker slice: mount root only,
    no dead picker element fetching nowhere.
    """
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("data-stx-project-picker" not in body for body in bodies)


def test_storage_header_stamps_no_provider_url_without_provider(tmp_path):
    """Without a host provider no provider URL is stamped anywhere.

    Guards the fail-closed direction: no setting means no
    ``data-provider-url`` attribute the picker transport could read.
    """
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("data-provider-url" not in body for body in bodies)


# EOF


def _leaf_storage_block():
    """Declarations of the leaf ``.stx-storage`` rule, comments stripped."""
    import re
    from pathlib import Path

    css = Path("src/scitex_storage/_django/static/scitex_storage/index.css").read_text()
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    match = re.search(r"^\.stx-storage \{(.*?)\}", stripped, re.M | re.S)
    if match is None:
        raise ValueError("leaf .stx-storage rule must still exist")
    return match.group(1)


def test_organize_roots_carry_shared_layout_fill(tmp_path):
    """Usage/Move/Duplicates roots use the SDK width contract, not a leaf rule."""
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "usage"}).content.decode(),
        _get(tmp_path, {"tab": "move"}).content.decode(),
        _get(tmp_path, {"tab": "duplicates"}).content.decode(),
    )
    # Assert
    assert all('class="stx-storage stx-layout-fill"' in body for body in bodies)


def test_backup_root_carries_shared_layout_fill(tmp_path):
    """Backup placeholder (index.html) carries the same shared root class."""
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "backup"}).content.decode()
    # Assert
    assert 'class="stx-storage stx-layout-fill"' in body


def test_templates_load_shared_sdk_layout_css(tmp_path):
    """Both templates link the SDK utility stylesheet (app_shell is a shim)."""
    # Arrange
    _boot()
    # Act
    bodies = (
        _get(tmp_path, {"tab": "backup"}).content.decode(),
        _get(tmp_path, {"tab": "usage"}).content.decode(),
    )
    # Assert
    assert all("scitex_sdk/ui/css/utils/layout.css" in body for body in bodies)


def test_leaf_sets_no_width_rule(tmp_path):
    """No competing ``width`` on the leaf root (SDK no-!important contract)."""
    # Arrange
    _boot()
    # Act
    import re

    props = re.findall(r"^\s*([a-z-]+)\s*:", _leaf_storage_block(), re.M)
    # Assert
    assert "width" not in props


def test_leaf_sets_no_max_width_rule(tmp_path):
    """No competing ``max-width`` on the leaf root (SDK no-!important contract)."""
    # Arrange
    _boot()
    # Act
    import re

    props = re.findall(r"^\s*([a-z-]+)\s*:", _leaf_storage_block(), re.M)
    # Assert
    assert "max-width" not in props


def test_move_plan_names_the_verify_content_gate(tmp_path):
    """The plan display states which gate clears the delete (tally default)."""
    # Arrange
    _boot()
    # Act
    body = _get(
        tmp_path,
        {
            "tab": "move",
            "plan": "1",
            "volume": "mine",
            "dir": "",
            "destination": "scitex-nas-01",
        },
    ).content.decode()
    # Assert
    assert "--verify-content" in body


def test_leaf_bottom_reserve_stays_tight():
    """The dock reserve must not balloon the pane bottom (operator: 76px)."""
    # Arrange -- read the shipped stylesheet, not a constant.
    import re
    from pathlib import Path

    css = Path("src/scitex_storage/_django/static/scitex_storage/index.css").read_text()
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    match = re.search(r"^\.stx-storage \{(.*?)\}", body, re.M | re.S)
    # Act
    block = match.group(1)
    # Assert -- bottom = live dock height + 16px air, never the old +32px.
    assert " + 32px" not in block


def test_leaf_pane_padding_stays_tight():
    """Top/side pane padding stays minimal (operator: content fills pane)."""
    # Arrange -- read the shipped stylesheet, not a constant.
    import re
    from pathlib import Path

    css = Path("src/scitex_storage/_django/static/scitex_storage/index.css").read_text()
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    match = re.search(r"^\.stx-storage \{(.*?)\}", body, re.M | re.S)
    # Act
    block = match.group(1)
    # Assert -- 12px top / 16px sides; the old 20px/24px read as margins.
    assert "padding: 12px 16px calc(var(--site-dock-live-height, 0px) + 16px)" in block


class _ScopeUser:
    def __init__(self, username):
        self.username = username
        self.is_authenticated = True


class _ScopeProject:
    def __init__(self, path, name="Paper Project"):
        self._path = path
        self.name = name

    def get_local_path(self):
        from pathlib import Path

        return Path(self._path)


def _get_scoped(tmp_path, params, username=None, project_path=None, project_name="Paper Project"):
    """GET through the index view with an optional authed user + project seam."""
    from django.test import RequestFactory, override_settings

    from scitex_storage._django import project_files
    from scitex_storage._django.views import index

    _VOLUME_ROOT["path"] = str(tmp_path)
    dotted = f"{__name__}.provide_test_volumes"
    request = RequestFactory().get("/storage/", params)
    prev = project_files._GET_CURRENT_PROJECT
    if username is not None:
        request.user = _ScopeUser(username)

        def seam(request, user=None):
            return _ScopeProject(project_path, project_name)

        project_files._GET_CURRENT_PROJECT = seam
    try:
        with override_settings(SCITEX_STORAGE_VOLUMES_PROVIDER=dotted):
            return index(request)
    finally:
        project_files._GET_CURRENT_PROJECT = prev


def test_usage_shows_scope_control_defaulting_to_all_projects(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(tmp_path, {}).content.decode()
    # Assert
    assert "scope=project" in body


def test_usage_project_scope_without_project_names_no_project(tmp_path):
    # Arrange -- no authed user, so no project can resolve.
    _boot()
    # Act
    body = _get(tmp_path, {"tab": "usage", "scope": "project"}).content.decode()
    # Assert
    assert "No project in scope" in body


def test_usage_project_scope_scans_the_project_directory(tmp_path):
    # Arrange -- project dir holds the only file; the volume root is empty.
    _boot()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "paper.pdf").write_bytes(b"x" * 64)
    # Act
    body = _get_scoped(
        tmp_path, {"tab": "usage", "scope": "project"}, username="u", project_path=str(proj)
    ).content.decode()
    # Assert
    assert "Paper Project" in body


def test_move_project_scope_without_project_refuses_planning(tmp_path):
    # Arrange
    _boot()
    # Act
    body = _get(
        tmp_path,
        {"tab": "move", "scope": "project", "plan": "1", "volume": "mine",
         "dir": "", "destination": "scitex-nas-01"},
    ).content.decode()
    # Assert
    assert "No project in scope" in body


def test_move_project_scope_rejects_dir_outside_project(tmp_path):
    # Arrange -- project dir is a subdir; planning the volume root escapes it.
    _boot()
    proj = tmp_path / "proj"
    proj.mkdir()
    # Act
    response = _get_scoped(
        tmp_path,
        {"tab": "move", "scope": "project", "plan": "1", "volume": "mine",
         "dir": "", "destination": "scitex-nas-01"},
        username="u",
        project_path=str(proj),
    )
    # Assert
    assert response.status_code == 403


def test_move_project_scope_plans_dir_inside_project(tmp_path):
    # Arrange
    _boot()
    proj = tmp_path / "proj"
    (proj / "old-exp").mkdir(parents=True)
    ((proj / "old-exp") / "a.bin").write_bytes(b"x")
    # Act
    body = _get_scoped(
        tmp_path,
        {"tab": "move", "scope": "project", "plan": "1", "volume": "mine",
         "dir": "proj/old-exp", "destination": "scitex-nas-01"},
        username="u",
        project_path=str(proj),
    ).content.decode()
    # Assert
    assert "Move plan (not executed)" in body

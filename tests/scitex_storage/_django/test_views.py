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

    response = Client().get("/", {"path": str(tmp_path)})
    return response.context


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

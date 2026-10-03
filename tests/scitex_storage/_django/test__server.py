"""Real standalone App/UI boot and loopback requests, without a database."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

pytest.importorskip("django")

from scitex_storage._django._server import _port_in_use


@pytest.fixture
def standalone_pages(tmp_path):
    """Fresh real launcher and declared volume provider over fixture files."""
    if any(importlib.util.find_spec(name) is None for name in ("scitex_app", "scitex_ui")):
        pytest.skip("standalone rendering requires the complete gui extra")
    volume = tmp_path / "volume"
    volume.mkdir()
    (volume / "payload.bin").write_bytes(b"standalone fixture")
    source = Path(__file__).resolve().parents[3] / "src"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    script = tmp_path / "standalone.py"
    script.write_text(
        "import sys\n"
        "def fixture_volumes(request):\n"
        "    return [{'key': 'fixture', 'label': 'Fixture volume', "
        "'path': sys.argv[2], 'machine': 'fixture'}]\n"
        "from django.conf import settings\n"
        "settings.SCITEX_STORAGE_VOLUMES_PROVIDER = '__main__.fixture_volumes'\n"
        "from scitex_storage._django._server import run\n"
        "run(port=int(sys.argv[1]), host='127.0.0.1', open_browser=False)\n"
    )
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL") if key in os.environ}
    env.update({"PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1",
                "DJANGO_SETTINGS_MODULE": "scitex_storage._django.settings",
                "PYTHON_DOTENV_DISABLED": "1", "SCITEX_APP_MODE": "standalone",
                "SCITEX_DIR": str(tmp_path / "state")})
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, str(script), str(port), str(volume)],
                                   cwd=tmp_path, env=env, stdout=log, stderr=log)
        try:
            root = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError("standalone exited before serving: " + log.read())
                try:
                    with urlopen(root + "/healthz", timeout=0.2) as response:
                        response.read()
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("standalone did not become ready")
                    time.sleep(0.05)
            pages = {}
            for name, route in {"usage": "/?tab=usage&volume=fixture",
                                "css": "/static/scitex_ui/css/shell/app-shell.css"}.items():
                try:
                    with urlopen(root + route, timeout=2) as response:
                        pages[name] = response.status, response.read().decode()
                except HTTPError as error:
                    pages[name] = error.code, error.read().decode()
                    error.close()
            ui_source = Path(importlib.util.find_spec("scitex_ui").origin).parent
            pages["expected_css"] = (ui_source / "static/scitex_ui/css/shell/app-shell.css").read_bytes()
            yield pages
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def test_standalone_usage_page_returns_success(standalone_pages):
    # Arrange
    pages = standalone_pages
    # Act
    status = pages["usage"][0]
    # Assert
    assert status == 200


def test_standalone_usage_renders_leaf_inside_workspace_shell(standalone_pages):
    # Arrange
    pages = standalone_pages
    # Act
    body = pages["usage"][1]
    # Assert
    assert all(marker in body for marker in (
        'id="workspace-three-col"', 'id="stx-storage-app"',
        "Fixture volume", 'href="/sunburst/"', 'href="/bubbles/"', 'href="/fleet/"'))


def test_standalone_serves_shared_workspace_stylesheet(standalone_pages):
    # Arrange
    pages = standalone_pages
    # Act
    status, stylesheet = pages["css"]
    # Assert
    assert (status, hashlib.sha256(stylesheet.encode()).hexdigest()) == (
        200, hashlib.sha256(pages["expected_css"]).hexdigest())


def test_a_free_port_is_reported_available():
    # Ask the OS for an ephemeral port, release it, then probe: it must
    # read as free. A real socket, no mocks.
    # Arrange
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    # Act
    in_use = _port_in_use("127.0.0.1", port)

    # Assert
    assert in_use is False


def test_a_port_held_by_a_live_listener_is_reported_in_use():
    # A genuinely-bound LISTENING socket must read as in use -- the probe
    # sets SO_REUSEADDR (to ignore TIME_WAIT), so this proves it still
    # detects a real active listener rather than waving everything through.
    # Arrange
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    # Act
    result = _port_in_use("127.0.0.1", port)
    listener.close()

    # Assert
    assert result is True

# EOF

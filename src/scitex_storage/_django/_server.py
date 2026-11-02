#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/_server.py
"""Standalone Django GUI launcher for ``scitex-storage gui``.

Prepare the leaf's namespaced settings, then delegate through the isolated
App adapter to the same workspace shell used by the Hub mount. This leaf has
no database or migrations. The full App/UI shell is required; missing GUI
dependencies fail at startup instead of serving a partial interface.

Cloud/hub deployments do NOT use this module at all — they mount
``scitex_storage._django.urls`` into their own Django project (see
``urls.py``'s docstring).
"""

from __future__ import annotations

import os
import socket

import scitex_logging as slogging

# Django is the OPTIONAL ``gui`` extra (see pyproject.toml): this module
# only runs via ``scitex-storage gui``. Guarded per PS-233/PS-148 so the
# failure names the remedy.
try:
    import django
except ImportError as exc:
    raise ImportError(
        "scitex-storage GUI server requires Django: "
        "pip install scitex-storage[gui]"
    ) from exc


def _port_in_use(host: str, port: int) -> bool:
    """Whether ``port`` is genuinely unavailable to the server we launch.

    Must set ``SO_REUSEADDR`` before the probe bind, because that is what
    Django's ``runserver`` does. Without it, a socket left in ``TIME_WAIT``
    by a JUST-stopped instance makes this probe report "in use" for ~60s
    even though the real server WOULD bind successfully -- so a restart
    right after a stop fails with a bogus "port in use", which is exactly
    what a human hits when they stop and immediately start again. Matching
    the flag makes the probe agree with the server it is guarding.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
    except OSError:
        return True
    return False


def _print_banner(host: str, port: int) -> None:
    """Emit startup guidance at the current level without rerouting warnings."""
    console = slogging.getConsole(f"{__name__}.console", level=slogging.get_level())
    console.info(f"SciTeX Storage GUI: http://{host}:{port}")
    # "Ctrl+C" is only useful while you still have the terminal. Name the
    # commands that work AFTER it is gone -- the operator asked how to stop
    # the GUI and the banner had no answer for the case that actually
    # happens (started earlier, terminal closed, still listening).
    console.info("Stop: Ctrl+C here, or `scitex-storage gui stop` from anywhere")
    console.info("Check: `scitex-storage gui status`")


def run(
    port: int = 5051,
    host: str = "127.0.0.1",
    open_browser: bool = True,
    hot_reload: bool = False,
) -> None:
    """Launch the standalone GUI server.

    Requires ``_app_adapter.run_standalone`` for the full App/UI shell.
    The leaf's URL wrapper must be configured before delegation: mounting
    ``urls`` directly as the root loses the namespace used by its templates.

    Fails loud if ``port`` is already taken -- never silently binds a
    different one. ``gui serve``/``gui open`` bind the fleet's fixed
    3129X-block port (19_gui-commands.md doctrine: "no incrementing on
    repeated starts"); a server that silently drifts to a different
    port is lying about where it is.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")

    if _port_in_use(host, port):
        raise RuntimeError(
            f"Port {port} on {host} is already in use -- refusing to silently "
            f"bind a different port. Stop whatever's using {port} (or run "
            f"`scitex-storage gui status` to check if a previous instance is "
            f"still up) and retry."
        )
    _print_banner(host, port)

    django.setup()
    try:
        from ._app_adapter import run_standalone
        run_standalone(
            app_module="scitex_storage._django",
            port=port,
            host=host,
            open_browser=open_browser,
            hot_reload=hot_reload,
        )
    except ImportError as exc:
        raise RuntimeError(
            "The standalone Storage GUI requires the full App/UI shell. "
            "Install with: uv pip install 'scitex-storage[gui]'"
        ) from exc


# EOF

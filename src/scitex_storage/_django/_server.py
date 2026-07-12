#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/_server.py
"""Standalone local-dev launcher for the scitex-storage GUI (``scitex-storage start-gui``).

Tries the isolated adapter's ``run_standalone`` first (delegates to
``scitex_app._standalone.run_standalone``, which pre-wires scitex-ui's
static assets + the workspace shell so the local server looks like
scitex.ai/apps/storage). Falls back to a bare Django ``runserver`` if
scitex-app is not installed — mirrors scitex-writer's documented
fallback pattern exactly (``scitex_writer._django._server``).

Cloud/hub deployments do NOT use this module at all — they mount
``scitex_storage._django.urls`` into their own Django project (see
``urls.py``'s docstring).
"""

from __future__ import annotations

import os
import socket
import threading
import webbrowser


def _find_available_port(host: str, start_port: int) -> int:
    port = start_port
    for _ in range(100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return port
        except OSError:
            port += 1
    return start_port


def run(
    port: int = 5051,
    host: str = "127.0.0.1",
    open_browser: bool = True,
    hot_reload: bool = False,
) -> None:
    """Launch the standalone GUI server.

    Tries ``_app_adapter.run_standalone`` first (gets the full workspace
    shell from scitex-ui via scitex-app). Falls back to a bare
    ``runserver`` bootstrap if scitex-app is not installed.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")

    port = _find_available_port(host, port)
    print(f"SciTeX Storage GUI: http://{host}:{port}")
    print("Press Ctrl+C to stop")

    try:
        import django

        django.setup()

        from django.core.management import call_command

        call_command("migrate", "--run-syncdb", verbosity=0)

        from ._app_adapter import run_standalone

        run_standalone(
            app_module="scitex_storage._django",
            port=port,
            host=host,
            open_browser=open_browser,
            hot_reload=hot_reload,
        )
        return
    except ImportError:
        pass

    # Fallback: no scitex-app available, run bare Django.
    import django

    django.setup()

    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=[f"http://{host}:{port}"]).start()

    from django.core.management import call_command

    noreload = [] if hot_reload else ["--noreload"]
    call_command("runserver", f"{host}:{port}", *noreload)


# EOF

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

The fallback is LOUD. It used to be silent (``except ImportError: pass``
wrapped around the ``run_standalone`` CALL), which meant a missing
scitex-app served an unstyled page with no explanation — reported as
"this looks weird" rather than as a missing dependency, because the page
rendered. Degrading is fine; degrading quietly is not, and a page that
renders hides its own degradation far better than one that fails.

Cloud/hub deployments do NOT use this module at all — they mount
``scitex_storage._django.urls`` into their own Django project (see
``urls.py``'s docstring).
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser


def bare_django_warning(cause: BaseException | None) -> str:
    """Render the warning shown when the scitex-app shell is unavailable.

    Pure so the wording is testable without standing up a server. The
    text must name the CAUSE, the EFFECT, and the REMEDY: a degraded
    page that renders is harder to notice than one that fails, so the
    warning is the only thing distinguishing "unstyled because broken"
    from "unstyled because that is how it looks".
    """
    return (
        "\n"
        "  WARNING: serving BARE DJANGO -- the scitex-app shell is unavailable.\n"
        f"    cause:  {cause}\n"
        "    effect: the page renders UNSTYLED -- no workspace shell, no theme,\n"
        "            no favicon. It looks broken because it IS degraded.\n"
        "    remedy: pip install scitex-app\n"
    )


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
    except OSError:
        return True
    return False


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
    print(f"SciTeX Storage GUI: http://{host}:{port}")
    # "Ctrl+C" is only useful while you still have the terminal. Name the
    # commands that work AFTER it is gone -- the operator asked how to stop
    # the GUI and the banner had no answer for the case that actually
    # happens (started earlier, terminal closed, still listening).
    print("Stop: Ctrl+C here, or `scitex-storage gui stop` from anywhere")
    print("Check: `scitex-storage gui status`")

    import django

    django.setup()

    from django.core.management import call_command

    call_command("migrate", "--run-syncdb", verbosity=0)

    # The adapter imports cleanly even when scitex-app is absent -- it defers
    # the ImportError to CALL time on purpose. So both the import and the call
    # have to be caught, and neither may be swallowed: the whole point of that
    # deferral is defeated by a bare ``except ImportError: pass`` around the
    # call, which is what shipped and is why an unstyled page looked like a
    # working one.
    shell_unavailable: ImportError | None = None
    try:
        from ._app_adapter import run_standalone
    except ImportError as exc:  # adapter itself missing
        shell_unavailable = exc
    else:
        try:
            run_standalone(
                app_module="scitex_storage._django",
                port=port,
                host=host,
                open_browser=open_browser,
                hot_reload=hot_reload,
            )
            return
        except ImportError as exc:  # scitex-app missing, raised on call
            shell_unavailable = exc

    # Fallback: no scitex-app available, run bare Django -- LOUDLY.
    #
    # This module already refuses to silently bind a different port (see the
    # RuntimeError above); serving a silently unstyled page is the same class
    # of lie about what you are getting, and it is harder to notice because
    # the page renders. Degrading is acceptable; degrading QUIETLY is not.
    print(bare_django_warning(shell_unavailable), file=sys.stderr)

    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=[f"http://{host}:{port}"]).start()

    from django.core.management import call_command

    noreload = [] if hot_reload else ["--noreload"]
    call_command("runserver", f"{host}:{port}", *noreload)


# EOF

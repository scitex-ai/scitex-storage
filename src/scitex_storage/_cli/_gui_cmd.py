#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage gui {open,serve,status,stop}`` — the canonical GUI lifecycle group.

Per scitex-dev's fleet-wide GUI-commands doctrine
(``_skills/general/03_interface/02_cli/19_gui-commands.md``): every
browser-based surface mounts under one group, ``gui``, with exactly
four fixed verbs. ``gui`` itself takes no positional argument (a
``gui [SOURCE]`` leaf shape breaks Click's subcommand resolution).

State (pid/port/host) is tracked via ``scitex_dev.gui_runtime.GuiRuntime``
at ``~/.scitex/scitex-storage/runtime/gui.json`` — the same runtime-path
convention this repo already uses for archive manifests
(``_archive.py``'s ``~/.scitex/scitex-storage/runtime/archive-manifests``).

Always registered on ``main`` (importing this module needs only
``click``, never Django) so ``scitex-storage --help`` always lists
``gui`` — but ``serve``/``open`` require the ``gui`` optional-dependency
group (Django + scitex-app + scitex-ui; see ``pyproject.toml``). Without
it, both raise a clear, actionable ``click.ClickException`` rather than
a raw ``ImportError`` traceback.

``start-gui`` (the pre-unification leaf) stays as a Phase-W hidden alias
forwarding to ``gui open`` (per scitex-dev's deprecation-ladder doctrine,
``11_deprecation.md``) -- it always opened a browser by default, which is
``open``'s semantics, not headless ``serve``'s.
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from ._compat import spec_command_kwargs, spec_group_kwargs

FIXED_PORT = 31290
FIXED_HOST = "127.0.0.1"


def _runtime():
    from scitex_dev.gui_runtime import GuiRuntime

    state_path = Path("~/.scitex/scitex-storage/runtime/gui.json").expanduser()
    return GuiRuntime(state_path)


def _require_django():
    try:
        import django  # noqa: F401

        return django
    except ImportError as e:
        raise click.ClickException(
            "The scitex-storage GUI requires Django. Install with: "
            f"pip install scitex-storage[gui]  ({e})"
        ) from e


def _warn_once_forward(old_name: str, new_invocation: str, removed_in: str) -> None:
    """Phase-W once-per-shell stderr warning (keyed by $PPID), per 11_deprecation.md."""
    marker_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    ppid = os.environ.get("PPID") or str(os.getppid())
    marker = marker_dir / f"scitex-cli-dep-{os.environ.get('USER', 'user')}-{ppid}-{old_name}.flag"
    if marker.exists():
        return
    click.echo(
        f"'{old_name}' is deprecated -- use '{new_invocation}' (removed in {removed_in})",
        err=True,
    )
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass


@click.group(
    "gui",
    **spec_group_kwargs(
        summary="Launch, check, or stop the browser-based scitex-storage GUI.",
    ),
)
def gui_group() -> None:
    pass


@gui_group.command(
    "serve",
    **spec_command_kwargs(
        summary="Run the GUI server in the FOREGROUND (headless, no browser).",
        description=(
            "Boots scitex-storage's Django GUI plugin (the same _django app "
            "scitex-hub mounts under /storage/) and blocks until Ctrl-C. "
            f"Binds the fixed scitex-storage GUI port ({FIXED_PORT}), the "
            "package's slot in the fleet's shared 3129X standalone-GUI "
            "block. Never opens a browser -- that's `gui open`'s job. "
            "Requires the `gui` optional-dependency group: "
            "`pip install scitex-storage[gui]`.",
        ),
        examples=(
            (f"{{prog}} gui serve", f"foreground server at http://{FIXED_HOST}:{FIXED_PORT}/"),
            (f"{{prog}} gui serve --host 0.0.0.0", "bind all interfaces"),
        ),
    ),
)
@click.option(
    "--host", default=FIXED_HOST, show_default=True, help="Bind host."
)
@click.option(
    "--hot-reload", is_flag=True, help="Enable Django's auto-reloader (dev only)."
)
def gui_serve_cmd(host: str, hot_reload: bool) -> None:
    _require_django()
    from .._django._server import run

    runtime = _runtime()
    runtime.write_state(os.getpid(), FIXED_PORT, host)
    try:
        run(port=FIXED_PORT, host=host, open_browser=False, hot_reload=hot_reload)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    finally:
        runtime.clear_state()


@gui_group.command(
    "open",
    **spec_command_kwargs(
        summary="Open the GUI in a browser, auto-serving first if not running.",
        description=(
            "The user-facing entry point: if the GUI isn't already running "
            "(per `gui status`), starts it in the background first, then "
            "opens a browser tab at its URL. If it's already running, just "
            "opens the browser -- never starts a second instance.",
        ),
        examples=((f"{{prog}} gui open", "auto-serve (if needed) + open browser"),),
    ),
)
@click.option(
    "--host", default=FIXED_HOST, show_default=True, help="Bind host (only used if not already running)."
)
def gui_open_cmd(host: str) -> None:
    import threading
    import webbrowser

    _require_django()

    runtime = _runtime()
    current = runtime.status()
    if current.get("running"):
        url = current["url"]
        click.echo(f"Already running: {url}")
        webbrowser.open(url)
        return

    from .._django._server import run

    url = f"http://{host}:{FIXED_PORT}"
    click.echo(f"Starting scitex-storage GUI: {url}")
    threading.Timer(1.0, webbrowser.open, args=[url]).start()
    runtime.write_state(os.getpid(), FIXED_PORT, host)
    try:
        run(port=FIXED_PORT, host=host, open_browser=False, hot_reload=False)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    finally:
        runtime.clear_state()


@gui_group.command(
    "status",
    **spec_command_kwargs(
        summary="Report whether the GUI server is running, and where.",
        examples=(
            (f"{{prog}} gui status", "print running/not-running + URL"),
            (f"{{prog}} gui status --json", "machine-readable output"),
        ),
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def gui_status_cmd(as_json: bool) -> None:
    import json as json_mod

    state = _runtime().status()
    if as_json:
        click.echo(json_mod.dumps(state))
        return
    if state.get("running"):
        click.echo(f"running: {state['url']} (pid {state['pid']})")
    else:
        click.echo("not running")


@gui_group.command(
    "stop",
    **spec_command_kwargs(
        summary="Stop the running GUI server instance.",
        examples=(
            (f"{{prog}} gui stop", "stop it, if running"),
            (f"{{prog}} gui stop --dry-run", "report what would be stopped, don't stop it"),
        ),
    ),
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Report what would be stopped, without stopping it.",
)
@click.option(
    "--yes",
    "-y",
    "confirmed",
    is_flag=True,
    help="Bypass interactive confirmation (no-op today -- no prompt exists yet).",
)
def gui_stop_cmd(dry_run: bool, confirmed: bool) -> None:
    del confirmed  # accepted for universal-flag conformance; no prompt to bypass yet
    if dry_run:
        state = _runtime().status()
        if state.get("running"):
            click.echo(f"Would stop: {state['url']} (pid {state['pid']})")
        else:
            click.echo("not running -- nothing to stop")
        return

    result = _runtime().stop()
    if result.get("stopped"):
        click.echo(f"stopped (pid {result['pid']})")
    elif not result.get("running"):
        click.echo("not running")
    else:
        raise click.ClickException(f"failed to stop pid {result.get('pid')}: {result.get('error')}")


@click.command(
    "start-gui",
    hidden=True,
    **spec_command_kwargs(
        summary="Deprecated -- use 'gui open'.",
        examples=((f"{{prog}} gui open", "the replacement invocation"),),
    ),
)
@click.option("--host", default=FIXED_HOST, show_default=True)
@click.option("--port", type=int, default=None, hidden=True)
@click.option("--no-browser", "no_browser", is_flag=True)
@click.option("--hot-reload", is_flag=True)
@click.option("--dry-run", "dry_run", is_flag=True)
@click.option("--yes", "-y", "confirmed", is_flag=True)
@click.pass_context
def start_gui_cmd(
    ctx: click.Context,
    host: str,
    port: int | None,
    no_browser: bool,
    hot_reload: bool,
    dry_run: bool,
    confirmed: bool,
) -> None:
    del port, confirmed  # legacy leaf let the caller pick a port; gui group fixes it
    _warn_once_forward("start-gui", "gui open", "v0.4.0")
    if dry_run:
        click.echo(
            f"Would launch scitex-storage GUI at http://{host}:{FIXED_PORT}/ "
            f"(settings: scitex_storage._django.settings, hot_reload={hot_reload})"
        )
        return
    if no_browser:
        ctx.invoke(gui_serve_cmd, host=host, hot_reload=hot_reload)
    else:
        ctx.invoke(gui_open_cmd, host=host)


# EOF

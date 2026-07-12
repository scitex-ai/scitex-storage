#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage start-gui`` — launch the standalone Django GUI plugin.

Always registered on ``main`` (importing this module needs only
``click``, never Django) so ``scitex-storage --help`` always lists
``start-gui`` — but *running* it requires the ``gui`` optional-dependency
group (Django + scitex-app + scitex-ui; see ``pyproject.toml``'s
``[project.optional-dependencies]``). Without it, ``start_gui_cmd`` raises
a clear, actionable ``click.ClickException`` rather than a raw
``ImportError`` traceback.

Named ``start-gui`` (verb-first), not the noun ``gui`` — this repo's own
``scitex-dev ecosystem audit-cli`` (§1) rejects noun-only leaf tokens
that imply a transitive action, and explicitly suggests ``start-<noun>``
as the fix. ``start`` is itself classified as a MUTATING lifecycle verb
(§2), which is why ``--dry-run`` and ``--yes``/``-y`` are wired below
even though launching a local dev server has no destructive side effect
to preview or confirm today -- ``--dry-run`` resolves and prints what
WOULD be bound (host/port/settings module) without binding a socket;
``--yes`` is accepted for universal-flag conformance and is forward
compatible with a future confirm-before-bind prompt (e.g. binding
``0.0.0.0``).
"""

from __future__ import annotations

import click

from ._compat import spec_command_kwargs


@click.command(
    "start-gui",
    **spec_command_kwargs(
        summary="Launch the standalone scitex-storage GUI (browser-based scan viewer).",
        description=(
            "Boots a local Django server serving scitex-storage's GUI "
            "plugin (the same _django app scitex-hub mounts under "
            "/storage/), pre-wired with scitex-ui's workspace shell when "
            "scitex-app is installed. First scaffold: shows one page (a "
            "scan() table for a chosen path), not the full disk-treemap "
            "UI yet. Requires the `gui` optional-dependency group: "
            "`pip install scitex-storage[gui]`.",
        ),
        examples=(
            ("{prog} start-gui", "launch at http://127.0.0.1:5051/"),
            ("{prog} start-gui --port 9000 --no-browser", "custom port, no auto-open"),
            ("{prog} start-gui --dry-run", "print what would be bound, don't launch"),
        ),
    ),
)
@click.option(
    "--host", default="127.0.0.1", show_default=True, help="Bind host."
)
@click.option(
    "--port", type=int, default=5051, show_default=True, help="Bind port (auto-bumped if taken)."
)
@click.option(
    "--no-browser", "no_browser", is_flag=True, help="Don't auto-open a browser tab."
)
@click.option(
    "--hot-reload", is_flag=True, help="Enable Django's auto-reloader (dev only)."
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Print what would be bound (host/port/settings module) without launching.",
)
@click.option(
    "--yes",
    "-y",
    "confirmed",
    is_flag=True,
    help="Bypass interactive confirmation (no-op today -- no prompt exists yet).",
)
def start_gui_cmd(
    host: str,
    port: int,
    no_browser: bool,
    hot_reload: bool,
    dry_run: bool,
    confirmed: bool,
) -> None:
    del confirmed  # accepted for universal-flag conformance; no prompt to bypass yet

    if dry_run:
        click.echo(
            "Would launch scitex-storage GUI at "
            f"http://{host}:{port}/ (settings: scitex_storage._django.settings, "
            f"hot_reload={hot_reload}, open_browser={not no_browser})"
        )
        return

    try:
        import django  # noqa: F401

        from .._django._server import run
    except ImportError as e:
        raise click.ClickException(
            "The scitex-storage GUI requires Django. Install with: "
            f"pip install scitex-storage[gui]  ({e})"
        ) from e

    run(port=port, host=host, open_browser=not no_browser, hot_reload=hot_reload)


# EOF

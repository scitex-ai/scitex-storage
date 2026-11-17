#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/_app_adapter.py
"""Optional GUI adapter for the public SDK App embedding API.

Keep the AppConfig and standalone launcher behind one public seam. The core
CLI does not require Django or SDK GUI dependencies. Importing the adapter
can use Django's AppConfig when SDK is absent; launching the GUI requires the
full SDK App/UI shell and raises an actionable error when it is unavailable.
"""

from __future__ import annotations

try:
    from scitex_sdk.app.embed import ScitexAppConfig as _ScitexAppConfig

    if _ScitexAppConfig is None:
        raise ImportError("SDK AppConfig requires Django integration")
except ImportError:
    # Fallback import needs its OWN guard: an import inside an except
    # handler is not protected by that handler (PS-233).
    try:
        from django.apps import (
            AppConfig as _ScitexAppConfig,  # type: ignore[assignment]
        )
    except ImportError as exc:
        raise ImportError(
            "scitex-storage GUI adapter requires Django or scitex-sdk app: "
            "pip install scitex-storage[gui]"
        ) from exc

ScitexAppConfig = _ScitexAppConfig


def run_standalone(*args, **kwargs):
    """Launch through the public SDK API, imported only when called."""
    try:
        from scitex_sdk.app.embed import run_standalone as _run_standalone
    except ImportError as exc:
        raise ImportError(
            "scitex-storage standalone server requires scitex-sdk app: "
            "pip install scitex-storage[gui]"
        ) from exc

    return _run_standalone(*args, **kwargs)


# EOF

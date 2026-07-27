"""Unit tests for scitex_storage._django._server.

Covers the bare-Django fallback WARNING, which is the part that shipped
broken: `run()` caught `ImportError` around the `run_standalone(...)`
CALL and `pass`ed, so a missing scitex-app produced a silently unstyled
page. A page that renders is far harder to notice as degraded than one
that fails, so the warning text is the entire user-facing signal.

`test__app_adapter.py` records that the "scitex-app absent" branch could
not be exercised honestly without `monkeypatch` (banned here). Extracting
the wording into a PURE function removes that obstacle for the part that
actually matters -- what the operator is told -- with no mocks and no
server.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

import socket

from scitex_storage._django._server import _port_in_use, bare_django_warning


def test_warning_names_the_underlying_cause():
    # Arrange
    cause = ImportError("No module named 'scitex_app'")

    # Act
    text = bare_django_warning(cause)

    # Assert
    assert "No module named 'scitex_app'" in text


def test_warning_names_the_remedy():
    # Arrange
    # Act
    text = bare_django_warning(ImportError("boom"))

    # Assert
    assert "pip install scitex-app" in text


def test_warning_states_the_page_is_unstyled():
    # Arrange
    # Act
    text = bare_django_warning(ImportError("boom"))

    # Assert
    assert "UNSTYLED" in text


def test_warning_states_which_server_is_actually_serving():
    # Arrange
    # Act
    text = bare_django_warning(ImportError("boom"))

    # Assert
    assert "BARE DJANGO" in text


def test_warning_survives_a_missing_cause():
    # A None cause must still produce a usable warning rather than
    # raising while reporting a failure.
    # Arrange
    # Act
    text = bare_django_warning(None)

    # Assert
    assert "pip install scitex-app" in text


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

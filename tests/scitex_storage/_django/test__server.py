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

from scitex_storage._django._server import bare_django_warning


def test_warning_names_the_underlying_cause():
    # Arrange
    cause = ImportError("No module named 'scitex_app'")

    # Act
    text = bare_django_warning(cause)

    # Assert
    assert "No module named 'scitex_app'" in text


def test_warning_names_the_remedy():
    # Act
    text = bare_django_warning(ImportError("boom"))

    # Assert
    assert "pip install scitex-app" in text


def test_warning_states_the_page_is_unstyled():
    # Act
    text = bare_django_warning(ImportError("boom"))

    # Assert
    assert "UNSTYLED" in text


def test_warning_states_which_server_is_actually_serving():
    # Act
    text = bare_django_warning(ImportError("boom"))

    # Assert
    assert "BARE DJANGO" in text


def test_warning_survives_a_missing_cause():
    # A None cause must still produce a usable warning rather than
    # raising while reporting a failure.
    # Act
    text = bare_django_warning(None)

    # Assert
    assert "pip install scitex-app" in text

# EOF

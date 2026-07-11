"""Unit tests for scitex_storage._cli._introspect (list-python-apis)."""

import json

from click.testing import CliRunner

from scitex_storage._cli._introspect import list_python_apis


def test_list_python_apis_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(list_python_apis, [])
    # Assert
    assert result.exit_code == 0


def test_list_python_apis_lists_scan_function():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(list_python_apis, [])
    # Assert
    assert "scan" in result.output


def test_list_python_apis_json_flag_emits_valid_json():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(list_python_apis, ["--json"])
    # Assert
    assert isinstance(json.loads(result.output), list)

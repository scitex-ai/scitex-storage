"""Unit tests for scitex_storage._cli._mcp_commands (mcp group stub)."""

import json

from click.testing import CliRunner

from scitex_storage._cli._mcp_commands import mcp


def test_mcp_list_tools_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(mcp, ["list-tools"])
    # Assert
    assert result.exit_code == 0


def test_mcp_list_tools_json_flag_reports_empty_tools():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(mcp, ["list-tools", "--json"])
    # Assert
    assert json.loads(result.output)["tools"] == []


def test_mcp_no_args_shows_help():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(mcp, [])
    # Assert
    assert result.exit_code == 0

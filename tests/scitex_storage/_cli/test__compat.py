"""Unit tests for scitex_storage._cli._compat (guarded CliHelp import)."""

import click

from scitex_storage._cli._compat import spec_command_kwargs, spec_group_kwargs


def test_spec_command_kwargs_returns_a_dict():
    # Arrange
    # Act
    kwargs = spec_command_kwargs(summary="Do a thing.", examples=(("{prog} do", ""),))
    # Assert
    assert isinstance(kwargs, dict)


def test_spec_command_kwargs_is_usable_as_click_command_kwargs():
    # Arrange
    kwargs = spec_command_kwargs(summary="Do a thing.", examples=(("{prog} do", ""),))
    # Act
    @click.command("do", **kwargs)
    def _do():
        pass

    # Assert
    assert _do.name == "do"


def test_spec_group_kwargs_returns_a_dict():
    # Arrange
    # Act
    kwargs = spec_group_kwargs(summary="Root group.", version_of="scitex-storage")
    # Assert
    assert isinstance(kwargs, dict)


def test_spec_group_kwargs_is_usable_as_click_group_kwargs():
    # Arrange
    kwargs = spec_group_kwargs(summary="Root group.", version_of="scitex-storage")
    # Act
    @click.group("root", **kwargs)
    def _root():
        pass

    # Assert
    assert _root.name == "root"

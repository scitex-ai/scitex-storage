"""Unit tests for scitex_storage._tiers (canonical destination-tier facts)."""

from scitex_storage._tiers import tier_of_destination


def test_tier1_unit_is_scitex_nas_03():
    # Arrange -- the tiers do NOT follow the numbering (2026-08-07 rename).
    # Act
    tier = tier_of_destination("scitex-nas-03")
    # Assert
    assert tier == "tier1"


def test_tier2_units_are_scitex_nas_01_and_02():
    # Arrange
    # Act
    tiers = (tier_of_destination("scitex-nas-01"), tier_of_destination("scitex-nas-02"))
    # Assert
    assert tiers == ("tier2", "tier2")


def test_retired_alias_resolves_before_tier_lookup():
    # Arrange -- callers pass what the user typed, including retired names.
    # Act
    tier = tier_of_destination("nas2")
    # Assert
    assert tier == "tier2"


def test_unknown_destination_is_unknown_never_guessed():
    # Arrange
    # Act
    tier = tier_of_destination("no-such-host")
    # Assert
    assert tier == "unknown"

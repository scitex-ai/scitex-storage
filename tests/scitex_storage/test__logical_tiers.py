"""Logical tier authorization and deterministic configured cost estimates."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from scitex_storage._logical_tiers import LogicalTier, logical_tiers


def test_unique_authorized_volumes_supply_only_their_declared_tiers():
    # Arrange
    # Arrange
    volumes = [
        SimpleNamespace(key="workspace", tier="hot"),
        SimpleNamespace(key="compute-home", tier="cool"),
    ]
    # Act
    tiers = logical_tiers(volumes)
    # Assert
    # Act
    # Assert
    assert [(tier.name, tier.available) for tier in tiers] == [
        ("hot", True),
        ("warm", False),
        ("cool", True),
        ("cold", False),
    ]


def test_configured_foreign_volume_never_becomes_available():
    # Arrange
    # Arrange
    bindings = {"cold": {"volume": "another-users-volume"}}
    # Act
    tier = logical_tiers([], bindings=bindings)[3]
    # Assert
    # Act
    # Assert
    assert not tier.available and "another-users-volume" not in str(
        tier.public_details()
    )


def test_public_tier_details_do_not_expose_archive_aliases():
    # Arrange
    # Arrange
    tier = logical_tiers(
        [], bindings={"cold": {"archive_destination": "scitex-nas-01"}}
    )[3]
    # Act
    details = tier.public_details(1_000_000_000)
    # Assert
    # Act
    # Assert
    assert (
        details["name"] == "cold"
        and "scitex-nas" not in str(details)
        and "binding" not in details
    )


def test_rates_use_exact_decimal_gigabytes_and_thirty_day_months():
    # Arrange
    # Arrange
    tier = logical_tiers(
        [], rates={"hot": {"currency": "USD", "per_gb_month": "0.30"}}
    )[0]
    # Act
    estimates = [tier.estimate(2_000_000_000, days).amount for days in (1, 7, 30)]
    # Assert
    # Act
    # Assert
    assert estimates == [Decimal("0.02"), Decimal("0.14"), Decimal("0.60")]


def test_missing_rates_are_not_fabricated_as_free_storage():
    # Arrange
    # Arrange
    tier = LogicalTier("cold")
    # Act
    estimate = tier.estimate(1_000_000_000, 30)
    # Assert
    # Act
    # Assert
    assert estimate.amount is None and estimate.currency is None


@pytest.mark.parametrize("rate", ["-1", "NaN", "Infinity", "bad", None, True])
def test_invalid_rates_are_rejected(rate):
    # Arrange
    # Arrange
    rates = {"hot": {"currency": "USD", "per_gb_month": rate}}
    # Act / Assert
    # Act
    # Assert
    with pytest.raises(ValueError):
        logical_tiers([], rates=rates)


def test_ambiguous_volumes_do_not_silently_choose_a_physical_target():
    # Arrange
    # Arrange
    volumes = [
        SimpleNamespace(key="one", tier="warm"),
        SimpleNamespace(key="two", tier="warm"),
    ]
    # Act
    tier = logical_tiers(volumes)[1]
    # Assert
    # Act
    # Assert
    assert not tier.available and "primary" in tier.unavailable_reason

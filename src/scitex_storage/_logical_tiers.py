"""Logical storage tiers and configured estimates; physical bindings stay private."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

TIER_NAMES = ("hot", "warm", "cool", "cold")
PERIOD_DAYS = (("Day", 1), ("Week", 7), ("Month (30 days)", 30))


@dataclass(frozen=True)
class StorageEstimate:
    days: int
    amount: Decimal | None
    currency: str | None


@dataclass(frozen=True)
class LogicalTier:
    name: str
    binding_kind: str | None = None
    binding: str | None = None
    rate_per_gb_month: Decimal | None = None
    currency: str | None = None
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.binding_kind is not None and self.binding is not None

    def estimate(self, size_bytes: int, days: int) -> StorageEstimate:
        if type(size_bytes) is not int or size_bytes < 0:
            raise ValueError("Storage size must be a nonnegative integer byte count")
        if type(days) is not int or days < 1:
            raise ValueError("Storage duration must be a positive integer day count")
        amount = None
        if self.rate_per_gb_month is not None:
            amount = (
                Decimal(size_bytes)
                / Decimal(1_000_000_000)
                * self.rate_per_gb_month
                * Decimal(days)
                / Decimal(30)
            )
        return StorageEstimate(days, amount, self.currency)

    def public_details(self, size_bytes: int | None = None) -> dict[str, Any]:
        """No paths, volume keys, or physical host aliases leave the adapter."""
        return {
            "name": self.name,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "rate_per_gb_month": self.rate_per_gb_month,
            "currency": self.currency,
            "estimates": []
            if size_bytes is None
            else [
                {
                    "label": label,
                    "days": days,
                    "amount": self.estimate(size_bytes, days).amount,
                    "currency": self.currency,
                }
                for label, days in PERIOD_DAYS
            ],
        }


def _price(value: Mapping[str, Any] | None) -> tuple[Decimal | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        raise ValueError("A tier rate must contain currency and per_gb_month")
    if not value:
        return None, None
    currency = value.get("currency")
    if not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
        raise ValueError("A configured rate needs a three-letter uppercase currency")
    try:
        rate = Decimal(str(value["per_gb_month"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        raise ValueError("A tier rate must be a finite nonnegative decimal") from None
    if not rate.is_finite() or rate < 0:
        raise ValueError("A tier rate must be a finite nonnegative decimal")
    return rate, currency


def logical_tiers(
    volumes: Iterable[Any],
    *,
    bindings: Mapping[str, Mapping[str, str]] | None = None,
    rates: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[LogicalTier, ...]:
    """Resolve only authorized volumes or explicitly configured archive hosts.

    With no binding, a unique authorized volume declaring a logical tier is
    used. Missing/ambiguous tiers remain unavailable; NAS names never imply
    a speed tier. Rates are optional operator configuration, not invented prices.
    """
    volumes = tuple(volumes)
    if any(
        not isinstance(getattr(volume, "key", None), str)
        or not volume.key
        or not isinstance(getattr(volume, "tier", None), str)
        for volume in volumes
    ):
        raise ValueError("Authorized volumes need string keys and tiers")
    by_key = {volume.key: volume for volume in volumes}
    bindings = {} if bindings is None else bindings
    rates = {} if rates is None else rates
    if not isinstance(bindings, Mapping) or not isinstance(rates, Mapping):
        raise ValueError("Tier bindings and rates must be mappings")
    if set(bindings) - set(TIER_NAMES) or set(rates) - set(TIER_NAMES):
        raise ValueError("Only hot, warm, cool, and cold tiers are supported")
    tiers = []
    for name in TIER_NAMES:
        rate, currency = _price(rates.get(name))
        binding = bindings.get(name)
        kind, target, reason = (
            None,
            None,
            "This tier is not configured for your account.",
        )
        if binding is not None:
            if not isinstance(binding, Mapping) or len(binding) != 1:
                raise ValueError(
                    "A tier binding names exactly one volume or archive destination"
                )
            if not all(isinstance(v, str) and v for v in binding.values()):
                raise ValueError("A tier binding needs one nonempty string target")
            if "volume" in binding:
                if binding["volume"] in by_key:
                    kind, target, reason = "volume", binding["volume"], ""
                else:
                    reason = "This tier is not available to your account."
            elif "archive_destination" in binding:
                from ._transfer._archive_transport import DESTINATIONS

                if binding["archive_destination"] not in DESTINATIONS:
                    raise ValueError(
                        "The configured archive destination is unsupported"
                    )
                kind, target, reason = "archive", binding["archive_destination"], ""
            else:
                raise ValueError("A tier binding names a volume or archive destination")
        else:
            matching = [volume for volume in volumes if volume.tier == name]
            if len(matching) == 1:
                kind, target, reason = "volume", matching[0].key, ""
            elif len(matching) > 1:
                reason = "A primary storage location must be configured for this tier."
        tiers.append(LogicalTier(name, kind, target, rate, currency, reason))
    return tuple(tiers)

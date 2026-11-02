"""Request-local illustrations, never configured billing or move inputs.

Warm/Cool/Cold examples are a snapshot of SciTeX Hub's published rate-card
source: apps/infra/public_app/data/pricing.json at commit
54f4a8691a5369569feb1cb87346d160abd1d747 (2026-11-02). Hot has no numeric
storage rate there: scratch is included with active compute. These editable
examples do not track commercial pricing or change tier availability.
"""

from decimal import Decimal, localcontext
import re

from scitex_storage._logical_tiers import PERIOD_DAYS, TIER_NAMES

_EXAMPLES = {"hot": "", "warm": "0.10", "cool": "0.03", "cold": "0.01"}
_DECIMAL = re.compile(r"(?:[0-9]{1,16}(?:\.[0-9]{0,9})?|\.[0-9]{1,9})")
_ERROR = "Use a nonnegative decimal with at most 16 integer and 9 fractional digits."


def _number(raw):
    """Bound formatting work as well as accepting finite, nonnegative values."""
    if _DECIMAL.fullmatch(raw) is None:
        return None, _ERROR
    return Decimal(raw), ""


def example_context(form):
    """Build editable USD illustrations without writing settings or user state."""
    size_raw = str(form.get("example_size_gb", "100")).strip()
    size, size_error = _number(size_raw)
    rows = []
    for name in TIER_NAMES:
        raw = str(form.get("example_rate_" + name, _EXAMPLES[name])).strip()
        rate, error = _number(raw) if raw else (None, "")
        estimates = []
        if size is not None and rate is not None:
            with localcontext() as context:
                context.prec = 50
                for label, days in PERIOD_DAYS:
                    amount = size * rate * Decimal(days) / Decimal(30)
                    estimates.append(
                        {"label": label, "amount": format(amount, ".6f")}
                    )
        rows.append(
            {"name": name, "rate": raw, "error": error, "estimates": estimates}
        )
    return {
        "example_size_gb": size_raw,
        "example_size_error": size_error,
        "example_tiers": rows,
    }

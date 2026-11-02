"""Display-only example arithmetic and bounded untrusted-input controls."""

import pytest

from scitex_storage._django._example_estimates import example_context


def test_reference_examples_use_exact_day_week_and_thirty_day_units():
    # Arrange -- the default size is explicitly illustrative, never account usage.
    form = {}
    # Act
    context = example_context(form)
    rows = {row["name"]: row for row in context["example_tiers"]}
    observed = {
        name: [item["amount"] for item in rows[name]["estimates"]]
        for name in ("warm", "cool", "cold")
    }
    # Assert
    assert observed == {
        "warm": ["0.333333", "2.333333", "10.000000"],
        "cool": ["0.100000", "0.700000", "3.000000"],
        "cold": ["0.033333", "0.233333", "1.000000"],
    }


def test_hot_is_not_assumed_to_be_free_storage():
    # Arrange
    form = {}
    # Act
    hot = example_context(form)["example_tiers"][0]
    # Assert
    assert (hot["rate"], hot["estimates"]) == ("", [])


def test_user_can_enter_a_zero_or_different_example_without_mutating_input():
    # Arrange
    form = {"example_size_gb": "50", "example_rate_hot": "0", "example_rate_warm": "0.20"}
    before = dict(form)
    # Act
    rows = example_context(form)["example_tiers"]
    observed = (rows[0]["estimates"][-1]["amount"], rows[1]["estimates"][-1]["amount"], form == before)
    # Assert
    assert observed == ("0.000000", "10.000000", True)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "1e999999999", "bad", "9" * 200, "0.0000000001"])
def test_invalid_size_shows_an_error_and_no_misleading_estimates(value):
    # Arrange
    form = {"example_size_gb": value}
    # Act
    context = example_context(form)
    observed = bool(context["example_size_error"]) and all(not row["estimates"] for row in context["example_tiers"])
    # Assert
    assert observed


@pytest.mark.parametrize("value", ["-0.1", "NaN", "Infinity", "1e999999999", "<script>"])
def test_invalid_rate_does_not_reuse_a_previous_or_default_estimate(value):
    # Arrange
    form = {"example_rate_warm": value}
    # Act
    row = example_context(form)["example_tiers"][1]
    # Assert
    assert bool(row["error"]) and row["estimates"] == []

"""
Tests for signals.py — reconciliation between the rule engine's level
and the ML model's probability.

Run with:  pytest tests/test_signals.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signals import reconcile_signals


def test_no_ml_probability_returns_nothing_to_compare():
    result = reconcile_signals("Low", None)
    assert result["agree"] is None
    assert result["note"] is None


def test_unknown_rule_level_returns_nothing_to_compare():
    result = reconcile_signals("Unknown", 0.5)
    assert result["agree"] is None
    assert result["note"] is None


def test_low_level_within_expected_band_agrees():
    result = reconcile_signals("Low", 0.2)
    assert result["agree"] is True
    assert result["note"] is None


def test_moderate_level_within_expected_band_agrees():
    result = reconcile_signals("Moderate", 0.5)
    assert result["agree"] is True
    assert result["note"] is None


def test_high_level_within_expected_band_agrees():
    result = reconcile_signals("High", 0.8)
    assert result["agree"] is True
    assert result["note"] is None


def test_low_level_but_high_ml_probability_disagrees_upward():
    result = reconcile_signals("Low", 0.9)
    assert result["agree"] is False
    assert result["note"] is not None
    assert "higher" in result["note"].lower()


def test_high_level_but_low_ml_probability_disagrees_downward():
    result = reconcile_signals("High", 0.1)
    assert result["agree"] is False
    assert result["note"] is not None
    assert "lower" in result["note"].lower()
    # Must not tell the student to disregard the trend.
    assert "not a reason to ignore" in result["note"].lower()


def test_reconciliation_never_returns_a_third_level():
    # Contract: this module only ever describes agreement, it must never
    # hand back something that looks like an overriding risk level.
    result = reconcile_signals("Moderate", 0.95)
    assert "level" not in result
    assert set(result.keys()) == {"agree", "note"}


def test_band_edges_are_inclusive():
    low, high = 0.25, 0.75  # Moderate band, per signals._EXPECTED_BAND
    assert reconcile_signals("Moderate", low)["agree"] is True
    assert reconcile_signals("Moderate", high)["agree"] is True
    assert reconcile_signals("Moderate", low - 0.01)["agree"] is False
    assert reconcile_signals("Moderate", high + 0.01)["agree"] is False

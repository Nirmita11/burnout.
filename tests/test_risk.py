"""
Tests for risk.py — the primary, explainable rule-based engine.

Run with:  pytest tests/test_risk.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from risk import analyze, describe_today, social_jetlag_hours, SOCIAL_JETLAG_THRESHOLD_HOURS


def make_day(study, sleep, mood, sleep_start=None, wake_time=None):
    return {
        "study_hours": study,
        "sleep_hours": sleep,
        "mood": mood,
        "sleep_start": sleep_start,
        "wake_time": wake_time,
    }


# ---------------------------------------------------------------------
# analyze() — empty / minimal input
# ---------------------------------------------------------------------

def test_analyze_no_logs_returns_no_data():
    result = analyze([])
    assert result["level"] == "No data"
    assert result["score"] == 0
    assert result["reasons"] == []


def test_analyze_single_day_does_not_crash():
    # n // 2 == 0 on a single row; earlier/recent windows must not divide by zero.
    result = analyze([make_day(3, 8, 4)])
    assert result["level"] in {"Low", "Moderate", "High"}


# ---------------------------------------------------------------------
# analyze() — trend-based scoring
# ---------------------------------------------------------------------

def test_stable_good_pattern_scores_low():
    days = [make_day(3, 8, 4) for _ in range(10)]
    result = analyze(days)
    assert result["level"] == "Low"
    assert result["score"] < 30


def test_rising_study_falling_sleep_falling_mood_scores_high():
    # Earlier half: light load, good sleep, good mood.
    # Recent half: heavy load, poor sleep, poor mood.
    earlier = [make_day(3, 8, 4) for _ in range(5)]
    recent = [make_day(6, 5, 2) for _ in range(5)]
    result = analyze(earlier + recent)
    assert result["level"] == "High"
    assert result["score"] >= 60
    assert any("Study" in r or "study" in r for r in result["reasons"])
    assert any("Sleep" in r or "sleep" in r for r in result["reasons"])
    assert any("Mood" in r or "mood" in r for r in result["reasons"])


def test_moderate_pattern_lands_in_moderate_band():
    earlier = [make_day(3, 7.5, 4) for _ in range(5)]
    recent = [make_day(4, 6.8, 3.5) for _ in range(5)]
    result = analyze(earlier + recent)
    assert result["level"] == "Moderate"
    assert 30 <= result["score"] < 60


def test_score_is_capped_at_100():
    earlier = [make_day(2, 9, 5) for _ in range(5)]
    recent = [make_day(10, 3, 1) for _ in range(5)]
    result = analyze(earlier + recent)
    assert result["score"] <= 100


def test_only_last_30_days_considered():
    # 40 bad days followed by 10 good days: only the last 30 should count,
    # so the trend should read as improving/stable, not catastrophic.
    bad = [make_day(9, 4, 1) for _ in range(40)]
    good = [make_day(3, 8, 4) for _ in range(10)]
    result = analyze(bad + good)
    assert result["avg_study"] < 9  # confirms the old bad days were trimmed off


# ---------------------------------------------------------------------
# social_jetlag_hours()
# ---------------------------------------------------------------------

def test_social_jetlag_none_with_insufficient_data():
    days = [make_day(3, 8, 4, "23:00", "07:00") for _ in range(3)]
    assert social_jetlag_hours(days) is None


def test_social_jetlag_none_when_no_timing_logged():
    days = [make_day(3, 8, 4) for _ in range(10)]
    assert social_jetlag_hours(days) is None


def test_social_jetlag_detects_consistent_bedtime_as_low():
    days = [make_day(3, 8, 4, "23:00", "07:00") for _ in range(6)]
    jetlag = social_jetlag_hours(days)
    assert jetlag is not None
    assert jetlag < SOCIAL_JETLAG_THRESHOLD_HOURS


def test_social_jetlag_detects_gradual_drift():
    # Bedtime creeping from 23:00 to 03:00 across the week — the exact
    # "gradual drift" case the value-split method is designed to catch
    # (a naive distance-from-median split would cancel this out).
    starts = ["23:00", "23:30", "00:00", "00:45", "01:30", "02:15", "03:00"]
    days = [make_day(3, 7, 4, s, "08:00") for s in starts]
    jetlag = social_jetlag_hours(days)
    assert jetlag is not None
    assert jetlag >= SOCIAL_JETLAG_THRESHOLD_HOURS


def test_social_jetlag_pushes_score_up_when_over_threshold():
    starts = ["23:00", "23:30", "00:00", "00:45", "01:30", "02:15", "03:00"]
    days = [make_day(3, 7.2, 4, s, "08:00") for s in starts]
    result = analyze(days)
    assert result["social_jetlag"] is not None
    assert result["social_jetlag"] >= SOCIAL_JETLAG_THRESHOLD_HOURS
    assert result["jetlag_reason"] is not None
    assert any("jetlag" in r.lower() or "swing" in r.lower() for r in result["reasons"])


# ---------------------------------------------------------------------
# describe_today()
# ---------------------------------------------------------------------

def test_describe_today_good_day():
    row = {"sleep_hours": 8, "mood": 5, "study_hours": 4}
    assert describe_today(row)["tone"] == "good"


def test_describe_today_tough_day_low_sleep():
    row = {"sleep_hours": 4, "mood": 3, "study_hours": 5}
    assert describe_today(row)["tone"] == "tough"


def test_describe_today_tough_day_low_mood():
    row = {"sleep_hours": 8, "mood": 1, "study_hours": 3}
    assert describe_today(row)["tone"] == "tough"


def test_describe_today_tough_day_long_study():
    row = {"sleep_hours": 8, "mood": 4, "study_hours": 9}
    assert describe_today(row)["tone"] == "tough"


def test_describe_today_neutral_day():
    row = {"sleep_hours": 6.5, "mood": 3, "study_hours": 6}
    assert describe_today(row)["tone"] == "neutral"


# ---------------------------------------------------------------------
# _good_reset() via analyze() — indirect, since it's a private helper
# ---------------------------------------------------------------------

def test_good_reset_flagged_after_rough_stretch():
    rough = [make_day(7, 5, 2) for _ in range(3)]
    reset_day = make_day(3, 8, 5)
    result = analyze(rough + [reset_day])
    assert result["good_reset"] is not None


def test_good_reset_not_flagged_without_rough_stretch():
    good_days = [make_day(3, 8, 4) for _ in range(4)]
    result = analyze(good_days)
    assert result["good_reset"] is None

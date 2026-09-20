"""
Tests for timetable.py — turns a risk level + the student's real subjects
into a day-by-day plan.

Run with:  pytest tests/test_timetable.py -v
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timetable import (
    DAYS,
    MAX_SITTING,
    _minutes,
    _round15,
    _split_into_sittings,
    _week_start,
    generate_weekly_timetable,
)


def make_flexible(name, hours, deadline_date=None, preferred_days=None):
    return {
        "name": name,
        "estimated_hours": hours,
        "deadline_date": deadline_date,
        "is_fixed": 0,
        "fixed_day": None,
        "fixed_time": None,
        "preferred_days": preferred_days,
    }


def make_fixed(name, day, time, hours=1):
    return {
        "name": name,
        "estimated_hours": hours,
        "deadline_date": None,
        "is_fixed": 1,
        "fixed_day": day,
        "fixed_time": time,
        "preferred_days": None,
    }


def blocks_for_day(plan, day_name):
    for day in plan["days"]:
        if day["day"] == day_name:
            return day["blocks"]
    raise AssertionError(f"{day_name} not found in plan")


# ---------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------

def test_round15_rounds_to_nearest_quarter_hour():
    assert _round15(10) == 15   # floors to the minimum, never zero
    assert _round15(50) == 45   # 50/15 = 3.33 -> rounds down to 3
    assert _round15(53) == 60   # 53/15 = 3.53 -> rounds up to 4
    assert _round15(60) == 60
    assert _round15(0) == 15


def test_split_into_sittings_no_split_needed():
    blocks, end = _split_into_sittings(_minutes("09:00"), 60, "Maths", "focus", MAX_SITTING, 15)
    assert len(blocks) == 1
    assert blocks[0][2] == "Maths"
    assert end == _minutes("10:00")


def test_split_into_sittings_splits_long_block_with_recovery():
    # 150 minutes at a 90-minute max sitting -> 90 + rest(15) + 60
    blocks, end = _split_into_sittings(_minutes("09:00"), 150, "Thesis", "fixed", MAX_SITTING, 15)
    kinds = [b[3] for b in blocks]
    assert kinds == ["fixed", "rest", "fixed"]
    assert blocks[0][2] == "Thesis"
    assert blocks[1][2] == "Recovery"
    # 09:00 + 90 + 15 + 60 = 11:45
    assert end == _minutes("11:45")


# ---------------------------------------------------------------------
# generate_weekly_timetable() — empty state
# ---------------------------------------------------------------------

def test_no_subjects_returns_genuinely_empty_plan():
    plan = generate_weekly_timetable("Low", subjects=[])
    assert plan["empty"] is True
    assert plan["days"] == []
    assert plan["subjects"] == []


def test_none_subjects_also_returns_empty_plan():
    plan = generate_weekly_timetable("Low", subjects=None)
    assert plan["empty"] is True


# ---------------------------------------------------------------------
# Fixed commitments
# ---------------------------------------------------------------------

def test_fixed_commitment_lands_on_its_own_day_only():
    subjects = [make_fixed("Lecture", "Wednesday", "10:00", hours=1)]
    plan = generate_weekly_timetable("Low", subjects=subjects)

    wed_blocks = blocks_for_day(plan, "Wednesday")
    assert any(b[2] == "Lecture" for b in wed_blocks)

    for day_name in DAYS:
        if day_name == "Wednesday":
            continue
        blocks = blocks_for_day(plan, day_name)
        assert not any(b[2] == "Lecture" for b in blocks)


def test_fixed_commitment_longer_than_max_sitting_gets_recovery_break():
    subjects = [make_fixed("Lab", "Monday", "09:00", hours=2)]  # 120 min > MAX_SITTING
    plan = generate_weekly_timetable("Low", subjects=subjects)
    mon_blocks = blocks_for_day(plan, "Monday")
    lab_blocks = [b for b in mon_blocks if b[2] == "Lab"]
    recoveries = [b for b in mon_blocks if b[2] == "Recovery"]
    assert len(lab_blocks) == 2
    assert len(recoveries) >= 1


# ---------------------------------------------------------------------
# Risk level shapes the plan (adaptive scheduling — the core promise)
# ---------------------------------------------------------------------

def test_high_risk_produces_shorter_or_equal_focus_blocks_than_low_risk():
    subjects = [make_flexible("Reading", 20)]  # plenty of hours, no deadline constraint

    low_plan = generate_weekly_timetable("Low", subjects=subjects)
    high_plan = generate_weekly_timetable("High", subjects=subjects)

    def focus_lengths(plan):
        lengths = []
        for day in plan["days"]:
            for start, end, label, kind in day["blocks"]:
                if kind == "focus":
                    lengths.append(_minutes(end) - _minutes(start))
        return lengths

    low_lengths = focus_lengths(low_plan)
    high_lengths = focus_lengths(high_plan)
    assert low_lengths and high_lengths
    assert max(high_lengths) <= max(low_lengths)


def test_high_risk_gives_longer_recovery_than_low_risk():
    subjects = [make_flexible("Reading", 10)]
    low_plan = generate_weekly_timetable("Low", subjects=subjects)
    high_plan = generate_weekly_timetable("High", subjects=subjects)
    assert high_plan["rest_len"] > low_plan["rest_len"]


# ---------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------

def test_flexible_subject_not_scheduled_after_its_deadline_day():
    week_start = _week_start()
    deadline_day_index = 2  # Wednesday
    deadline = (week_start + timedelta(days=deadline_day_index)).isoformat()

    subjects = [make_flexible("Essay", 20, deadline_date=deadline)]
    plan = generate_weekly_timetable("Low", subjects=subjects)

    for i, day_name in enumerate(DAYS):
        blocks = blocks_for_day(plan, day_name)
        has_essay = any(b[2] == "Essay" for b in blocks)
        if i > deadline_day_index:
            assert not has_essay, f"Essay should not appear on {day_name}, after its deadline"


def test_preferred_days_restrict_subject_to_chosen_days():
    subjects = [make_flexible("Project", 6, preferred_days="Monday,Friday")]
    plan = generate_weekly_timetable("Low", subjects=subjects)

    for day_name in DAYS:
        blocks = blocks_for_day(plan, day_name)
        has_project = any(b[2] == "Project" for b in blocks)
        if day_name in ("Monday", "Friday"):
            continue  # allowed to appear
        assert not has_project, f"Project should not appear on {day_name}"


# ---------------------------------------------------------------------
# Subjects summary view
# ---------------------------------------------------------------------

def test_subjects_view_labels_fixed_and_flexible_differently():
    subjects = [
        make_fixed("Class", "Monday", "09:00"),
        make_flexible("Reading", 3),
    ]
    plan = generate_weekly_timetable("Low", subjects=subjects)
    labels = {s["name"]: s["deadline"] for s in plan["subjects"]}
    assert labels["Class"].startswith("Fixed")
    assert labels["Reading"] == "No deadline set"

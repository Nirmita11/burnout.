"""
Weekly timetable suggestion.

There is exactly ONE source of truth for this feature: the `subjects`
table (see database.py's get_subjects/create_subject/update_subject/
delete_subject). This module never invents subjects of its own — if a
student has none saved yet, the caller gets back an empty plan and the
dashboard shows a genuine empty state, not an illustrative stand-in.

An earlier version of this file generated a fake "illustrative" week from
hardcoded mock subjects whenever the real table was empty, styled
identically to a real generated schedule. That was a real bug, not a
cosmetic one: a student would see a populated-looking week ("Mathematics,
Database Systems, Literature") while the actual Manage Subjects panel —
backed by the real table — was empty, and adding their first real subject
would make the mock subjects "disappear" since they were never real rows.
That mock path has been removed entirely.

All generated block times are rounded to clean 15-minute increments, and
any single sitting longer than MAX_SITTING is split into multiple blocks
with a recovery break between them — regardless of how much time is
available before a deadline.
"""

from datetime import date, timedelta

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_RISK_FACTOR = {"Low": 1.0, "Moderate": 0.8, "High": 0.6}
MAX_SITTING = 90          # no unbroken block longer than this
DEFAULT_REST_BASE = 15    # Low-risk baseline recovery length, unless the user set their own


def _minutes(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _fmt(total_minutes):
    total_minutes = max(0, total_minutes)
    return f"{(total_minutes // 60) % 24:02d}:{total_minutes % 60:02d}"


def _round15(m):
    """Rounds to the nearest 15-minute increment, minimum 15."""
    return max(15, int(round(m / 15.0)) * 15)


def _deadline_label(deadline_date, today=None):
    if not deadline_date:
        return "No deadline set"
    try:
        dd = date.fromisoformat(deadline_date)
    except ValueError:
        return deadline_date
    days = (dd - (today or date.today())).days
    if days < 0:
        return "Past due"
    if days == 0:
        return "Due today"
    if days == 1:
        return "Due tomorrow"
    return f"Due in {days} days"


def _deadline_sort_key(subject):
    d = subject.get("deadline_date")
    if not d:
        return date.max
    try:
        return date.fromisoformat(d)
    except ValueError:
        return date.max


def _week_start(today=None):
    """The calendar date the displayed plan's day 0 represents: today.

    Always today, not "the coming Monday" — an earlier version jumped
    forward to the next Monday whenever today wasn't one, which meant on
    any day but Monday the plan skipped the rest of the current week
    entirely (today included) and jumped up to six days ahead. The plan
    is meant to be "the week ahead starting now", not "next calendar
    week" — and today's own slice of it is also what the dashboard's
    "Today's Schedule" is built from, so it has to actually include today.
    """
    return today or date.today()


def _deadline_day_index(subject, week_start):
    """Which day of the displayed plan (0=today..6=six days out) a
    subject's deadline falls on, or None if it isn't due within this
    window (no deadline, or one further out than day 6) — in that case
    the subject isn't day-constrained and can use any day.

    A deadline that's already passed is clamped to day 0: all of its
    remaining work is maximally overdue, not something to keep drip-
    feeding across future days/weeks.
    """
    d = subject.get("deadline_date")
    if not d:
        return None
    try:
        dd = date.fromisoformat(d)
    except ValueError:
        return None
    delta = (dd - week_start).days
    if delta > 6:
        return None
    return max(0, delta)


def _preferred_day_indices(subject, deadline_idx, day_names):
    """Which positions in *this plan* (0=today..6=six days out) the
    student explicitly picked for this subject (from the "spread across
    specific days" picker on the subject form), or None if they left it
    to the automatic even spread.

    `preferred_days` is stored as absolute weekday names ("Tuesday"), so
    it has to be resolved against day_names — the real weekday name at
    each position of *this* plan, which starts from today and therefore
    shifts depending what day today is — not against the fixed DAYS
    list. DAYS is Monday-anchored (Monday=0) for stable storage/parsing,
    but plan positions are today-anchored (today=0); conflating the two
    made a picked "Tuesday" land on whatever position happened to be
    Monday+1 in the canonical list instead of the plan's actual Tuesday.

    A picked day past the subject's own deadline is dropped rather than
    honored — the deadline still wins over a stale day choice (e.g. the
    deadline got moved earlier after the days were picked).
    """
    raw = subject.get("preferred_days")
    if not raw:
        return None
    picked = {d for d in raw.split(",") if d in DAYS}
    idxs = sorted({day_names.index(d) for d in picked if d in day_names})
    if deadline_idx is not None:
        idxs = [i for i in idxs if i <= deadline_idx]
    return idxs or None


def _fixed_days(subject):
    """Which weekday names (lowercased) a fixed commitment recurs on.

    Reuses the same `preferred_days` field flexible tasks use to pick
    which days to spread across — a fixed commitment can meet more than
    once a week (e.g. a class on Tuesday AND Thursday), so it needed a
    multi-day picker too rather than the old single "Day" dropdown, and
    there was no reason to build a second picker when this one already
    does multi-day selection. `fixed_day` (the old single-value column)
    is still read as a fallback so commitments saved before this change
    keep working without a migration.
    """
    raw = subject.get("preferred_days")
    if raw:
        return {d.strip().lower() for d in raw.split(",") if d.strip()}
    single = subject.get("fixed_day")
    return {single.lower()} if single else set()


def _split_into_sittings(start_m, total_minutes, label, kind, max_sitting, rest_len):
    """
    Splits a single long commitment into <= max_sitting chunks with a
    rest_len recovery break between them. Any task/commitment longer than
    max_sitting gets broken up this way, regardless of how much room there
    is before its deadline. Returns (blocks, end_time_minutes).
    """

    blocks = []
    remaining = total_minutes
    cursor = start_m
    first = True

    while remaining > 0:
        if not first:
            blocks.append((_fmt(cursor), _fmt(cursor + rest_len), "Recovery", "rest"))
            cursor += rest_len

        chunk = min(max_sitting, remaining)
        end = cursor + chunk
        blocks.append((_fmt(cursor), _fmt(end), label, kind))
        cursor = end
        remaining -= chunk
        first = False

    return blocks, cursor


def _real_week(risk_level, subjects, base_rest_minutes=None, wake_time=None, today=None):
    """Builds a real day-by-day plan from the student's own subjects."""

    factor = _RISK_FACTOR.get(risk_level, 1.0)
    # The day's schedule starts at the student's own logged wake-up time
    # once they've given one; falls back to 09:00 otherwise.
    day_start = _minutes(wake_time) if wake_time else _minutes("09:00")

    # Already rounded to a clean 15-minute multiple when the user saved it
    # (see main.py's /preferences/recovery) — used as-is here.
    rest_base = base_rest_minutes if base_rest_minutes else DEFAULT_REST_BASE
    # Same growth *shape* as before (recovery lengthens as risk rises), but
    # in clean 15-minute steps from the user's own baseline instead of an
    # assumed one, and instead of a continuous formula that could collapse
    # Moderate/High into the same rounded value.
    rest_len = rest_base + {"Low": 0, "Moderate": 15, "High": 30}.get(risk_level, 0)

    # Not pre-rounded to 15 here: the chunk assignment loop below already
    # floors every emitted chunk to a clean 15-minute multiple, and floor
    # (unlike nearest-rounding) keeps Low/Moderate/High visibly distinct
    # instead of occasionally collapsing two tiers onto the same value.
    daily_budget = int(180 * factor)
    block_len = min(MAX_SITTING, int(45 * factor))

    fixed = [s for s in subjects if s.get("is_fixed")]
    flexible = sorted(
        (s for s in subjects if not s.get("is_fixed")),
        key=_deadline_sort_key,
    )
    remaining = [float(s.get("estimated_hours") or 0) for s in flexible]

    # Which DAYS index each subject's deadline falls on this week (or None
    # if it isn't day-constrained) — used below to spread its remaining
    # hours evenly across the days it actually has left, instead of
    # greedily maxing out whichever day it happens to be processed on
    # first and then sitting idle (or recurring uselessly past its own
    # deadline) for the rest of the week.
    week_start = _week_start(today)
    # Real calendar weekday names for each of the 7 days shown, starting
    # today — NOT the fixed DAYS list (that stays a Monday..Sunday
    # reference used only to parse/validate stored fixed_day/
    # preferred_days values, which are absolute weekday names).
    day_dates = [week_start + timedelta(days=i) for i in range(7)]
    day_names = [d.strftime("%A") for d in day_dates]
    deadline_idx = [_deadline_day_index(s, week_start) for s in flexible]

    # Days the student explicitly chose to spread this subject's hours
    # across (or None to keep the automatic even spread over every day
    # up to its deadline).
    preferred_idx = [
        _preferred_day_indices(s, deadline_idx[i], day_names) for i, s in enumerate(flexible)
    ]

    # On Low risk, if the nearest-deadline subject actually has a real
    # deadline, there's more room to push: give it heavier blocks instead
    # of the uniform length everyone else gets (still capped at MAX_SITTING).
    priority_index = None
    if risk_level == "Low" and flexible and flexible[0].get("deadline_date"):
        priority_index = 0
    priority_block_len = min(MAX_SITTING, block_len * 2)

    week = []
    for day_idx, day_name in enumerate(day_names):
        blocks = []
        todays_fixed = sorted(
            (s for s in fixed if day_name.lower() in _fixed_days(s)),
            key=lambda s: s.get("fixed_time") or "00:00",
        )
        fixed_windows = []
        for s in todays_fixed:
            start_m = _minutes(s.get("fixed_time") or "08:00")
            dur_m = _round15(int(float(s.get("estimated_hours") or 1) * 60) or 60)
            # Any commitment longer than MAX_SITTING still gets internal
            # recovery breaks — it can't move, but it can still be broken up.
            fixed_blocks, end_m = _split_into_sittings(
                start_m, dur_m, s["name"], "fixed", MAX_SITTING, rest_len
            )
            blocks.extend(fixed_blocks)
            fixed_windows.append((start_m, end_m))

        # Each subject's firm share of *today*, decided once before any
        # chunk is taken — not recomputed chunk-by-chunk from a shrinking
        # "remaining" — so a day-constrained subject can't quietly eat
        # more than its fair share of today just because it goes through
        # several passes before another subject gets a turn.
        day_allowance = []
        for i in range(len(flexible)):
            d_idx = deadline_idx[i]
            p_idx = preferred_idx[i]
            remaining_minutes = remaining[i] * 60

            if p_idx is not None:
                # Days the student explicitly picked for this subject —
                # only those days get any of it, split evenly between them.
                if day_idx not in p_idx:
                    day_allowance.append(0)
                    continue
                days_left = sum(1 for d in p_idx if d >= day_idx)
                day_allowance.append(remaining_minutes / days_left)
            elif d_idx is None:
                # Not day-constrained this week: same uniform cap as before.
                day_allowance.append(remaining_minutes)
            elif day_idx > d_idx:
                # Its deadline day has already gone by this week —
                # scheduling more of it now would be past due, not useful
                # catch-up.
                day_allowance.append(0)
            else:
                # No day picker used: spread whatever's left evenly across
                # today through its deadline day, so the work actually
                # lands on every day leading up to it instead of
                # front-loading a single day and going quiet until the
                # deadline (or past it).
                days_left = d_idx - day_idx + 1
                day_allowance.append(remaining_minutes / days_left)

        cursor = day_start
        budget_left = daily_budget

        # Subjects that asked for a preferred start time get placed first,
        # earliest-requested-time first, each taking its whole day
        # allowance in one sitting (split further only if it exceeds
        # MAX_SITTING) anchored at the later of "when they asked for" or
        # "whenever the day's already filled up to" — a request can't
        # rewind an earlier subject's block. Untimed subjects then fill
        # whatever's left via the round-robin below, same as before.
        timed_order = sorted(
            (
                i for i in range(len(flexible))
                if flexible[i].get("preferred_time") and remaining[i] > 0
                and budget_left > 0 and day_allowance[i] > 0
            ),
            key=lambda i: _minutes(flexible[i]["preferred_time"]),
        )
        for i in timed_order:
            subject = flexible[i]
            chunk = min(day_allowance[i], remaining[i] * 60, budget_left)
            chunk = int(chunk // 15) * 15
            if chunk < 15:
                continue

            start_m = max(_minutes(subject["preferred_time"]), cursor)
            for fs, fe in fixed_windows:
                if start_m < fe and start_m + chunk > fs:
                    start_m = fe

            timed_blocks, end_m = _split_into_sittings(
                start_m, chunk, subject["name"], "focus", MAX_SITTING, rest_len
            )
            blocks.extend(timed_blocks)
            cursor = end_m
            remaining[i] -= chunk / 60
            day_allowance[i] -= chunk
            budget_left -= chunk

        progressed = True
        while budget_left > 0 and progressed:
            progressed = False
            for i, subject in enumerate(flexible):
                if remaining[i] <= 0 or budget_left <= 0 or day_allowance[i] <= 0:
                    continue

                this_block_len = priority_block_len if i == priority_index else block_len
                chunk = min(this_block_len, day_allowance[i], remaining[i] * 60, budget_left)
                chunk = int(chunk // 15) * 15
                if chunk < 15:
                    continue

                start_m = cursor
                for fs, fe in fixed_windows:
                    if start_m < fe and start_m + chunk > fs:
                        start_m = fe

                end_m = start_m + chunk
                blocks.append((_fmt(start_m), _fmt(end_m), subject["name"], "focus"))
                cursor = end_m
                remaining[i] -= chunk / 60
                day_allowance[i] -= chunk
                budget_left -= chunk
                progressed = True

                rest_start, rest_end = cursor, cursor + rest_len
                blocks.append((_fmt(rest_start), _fmt(rest_end), "Recovery", "rest"))
                cursor = rest_end

        blocks.sort(key=lambda b: _minutes(b[0]))
        week.append({"day": day_name, "date": day_dates[day_idx].isoformat(), "blocks": blocks})

    subjects_view = [
        {
            "name": s["name"],
            "deadline": (
                f"Fixed · {(s.get('preferred_days') or s.get('fixed_day') or '').replace(',', ', ')} · {s.get('fixed_time', '')}".strip()
                if s.get("is_fixed")
                else _deadline_label(s.get("deadline_date"), week_start)
            ),
        }
        for s in sorted(subjects, key=_deadline_sort_key)
    ]

    return {
        "days": week,
        "subjects": subjects_view,
        "rest_len": rest_len,
    }


def generate_weekly_timetable(risk_level: str, subjects=None, base_rest_minutes=None, wake_time=None, today=None):
    """
    The single entry point. `subjects` must come from database.get_subjects()
    (via main.py) — there is no other source of schedule data. If the
    student hasn't added any yet, this returns an empty plan; the dashboard
    is responsible for showing a genuine "add your first subject" prompt
    rather than any placeholder schedule.
    """

    if not subjects:
        return {
            "days": [],
            "subjects": [],
            "rest_len": None,
            "empty": True,
        }
    return _real_week(risk_level, subjects, base_rest_minutes, wake_time, today)

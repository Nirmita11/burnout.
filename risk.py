SOCIAL_JETLAG_THRESHOLD_HOURS = 1.2  # Scientific Reports (2025), cited below

# Shown in-app (see "Why we ask this" on the dashboard) so this logic is
# never presented as if we invented it ourselves.
SLEEP_RESEARCH_SOURCES = [
    {
        "label": "Fitzsimmons, Carter & Scullin (2024), SLEEP — social jetlag predicts "
                 "lower GPA in first-year college students",
        "url": "https://academic.oup.com/sleep/article/47/Supplement_1/A64/7654204",
    },
    {
        "label": "Scientific Reports (2025) — depression risk rises significantly "
                 "beyond 1.2h of social jetlag in college students",
        "url": "https://www.nature.com/articles/s41598-025-03371-3",
    },
    {
        "label": "Systematic review/meta-analysis on social jetlag and mental health, "
                 "ScienceDirect (2025)",
        "url": "https://www.sciencedirect.com/science/article/pii/S0001691825007322",
    },
    {
        "label": "JMDH/Dove Medical Press — social jetlag linked to impaired attention, "
                 "executive function, working memory",
        "url": "https://www.dovepress.com/social-biological-and-behavioral-factors-"
               "associated-with-social-jet-la-peer-reviewed-fulltext-article-JMDH",
    },
]


def _sleep_midpoint_minutes(sleep_start, wake_time):
    """
    Minutes-after-previous-noon for the midpoint of one night's sleep.
    Anchoring at noon (rather than midnight) means a normal night's sleep
    (spanning midnight) never wraps around the anchor, so plain arithmetic
    works without special-casing "before midnight" vs "after midnight".
    """

    def to_minutes_after_noon(hhmm):
        h, m = (int(x) for x in hhmm.split(":"))
        return ((h * 60 + m) - 12 * 60) % (24 * 60)

    start = to_minutes_after_noon(sleep_start)
    end = to_minutes_after_noon(wake_time)
    if end <= start:
        end += 24 * 60
    return (start + end) / 2


def social_jetlag_hours(logs):
    """
    Social jetlag as this app defines it: the gap in sleep TIMING (not
    duration) between a student's more typical nights and their more
    shifted nights over the logged period — never a fixed "good bedtime"
    cutoff like "before 1am is fine".

    Method: take every night with both a sleep-start and wake-up time,
    find each night's sleep midpoint, sort all midpoints by clock value,
    and compare the average of the earlier-leaning half against the
    average of the later-leaning half.

    This is a value split, not a distance-from-median split. An earlier
    version split nights by how far each one sat from the median midpoint
    ("close to median" vs "far from median"). That works for a handful of
    one-off late nights, but silently fails for a gradual drift (e.g. a
    bedtime creeping from 11pm to 3am over two stressful weeks): the
    earliest nights and the latest nights both count as "far from the
    median" and end up in the same bucket, and their deviations point in
    opposite directions and cancel out when averaged — hiding a real,
    large swing. Splitting by value instead of by distance-from-center
    catches both a few erratic nights AND a steady drift, since either
    pattern pulls one half of the sorted midpoints later than the other.

    Research basis (also listed in-app, see SLEEP_RESEARCH_SOURCES):
      Scientific Reports (2025) — depression risk in college students rises
      significantly once social jetlag exceeds ~1.2 hours.
      https://www.nature.com/articles/s41598-025-03371-3

    Returns None if there isn't enough sleep-timing data logged yet.
    """

    midpoints = []
    for row in logs:
        start, wake = row.get("sleep_start"), row.get("wake_time")
        if start and wake:
            midpoints.append(_sleep_midpoint_minutes(start, wake))

    if len(midpoints) < 4:
        return None

    ordered = sorted(midpoints)
    half = len(ordered) // 2
    if half == 0:
        return None

    earlier_avg = sum(ordered[:half]) / half
    later_avg = sum(ordered[-half:]) / half

    return abs(later_avg - earlier_avg) / 60.0


def _good_reset(data):
    """A "good reset" signal: the most recent day reads as genuinely good
    on its own, right after a stretch of days that weren't.

    This is a distinct pattern from the trend deltas above — averaging a
    strong day in with several weaker ones just before it tends to wash
    out into an ambiguous "mixed" reading, and misses that the student's
    rebound is itself the notable thing happening.

    "Good" reuses describe_today's own bar (so this means the same thing
    it does on the single-day summary elsewhere on the dashboard), but
    "rough" deliberately does NOT require describe_today's "tough" tone —
    that bar is quite extreme (mood at rock bottom, under 6h sleep, or an
     8h+ day), and real rough patches are usually a run of merely
    lackluster days rather than back-to-back disasters. Here, any day
    that doesn't clear the "good" bar counts toward the stretch.

    Returns a short reason string, or None if the pattern isn't present
    (fewer than 4 days logged, the latest day isn't a good one, or there
    wasn't actually a stretch of lesser days right before it).
    """
    if len(data) < 4:
        return None
    if describe_today(data[-1])["tone"] != "good":
        return None

    lookback = data[-4:-1]
    rough = sum(1 for r in lookback if describe_today(r)["tone"] != "good")
    if rough >= 2:
        return "Last night looks like a real reset — solid sleep and mood right after a rough stretch."
    return None


def analyze(logs):
    """Explainable early-warning engine.

    Expects chronological logs with:
    study_hours, sleep_hours, mood
    """
    if not logs:
        return {
            "score": 0, "level": "No data", "reasons": [],
            "suggestion": "Log your first day to start seeing your pattern."
        }

    data = list(logs)[-30:]
    n = len(data)
    recent = data[max(0, n // 2):]
    earlier = data[:max(1, n // 2)]

    avg = lambda rows, key: sum(float(r[key]) for r in rows) / len(rows)
    earlier_study = avg(earlier, "study_hours")
    study_delta = avg(recent, "study_hours") - earlier_study
    sleep_delta = avg(recent, "sleep_hours") - avg(earlier, "sleep_hours")
    mood_delta = avg(recent, "mood") - avg(earlier, "mood")
    avg_sleep = avg(data, "sleep_hours")
    avg_mood = avg(data, "mood")
    avg_study = avg(data, "study_hours")

    # Percent framing for the "what we're noticing" signal chips.
    # Purely presentational — does not affect scoring.
    study_pct = round((study_delta / earlier_study) * 100) if earlier_study > 0 else 0

    score = 0
    reasons = []

    if study_delta >= 1.5:
        score += 30
        reasons.append("Study hours have risen sharply.")
    elif study_delta >= 0.75:
        score += 18
        reasons.append("Study load is trending upward.")

    if sleep_delta <= -1.0:
        score += 30
        reasons.append("Sleep has dropped noticeably.")
    elif sleep_delta <= -0.5:
        score += 18
        reasons.append("Sleep is trending downward.")

    if mood_delta <= -1.0:
        score += 25
        reasons.append("Mood has declined across the recent days.")
    elif mood_delta <= -0.5:
        score += 15
        reasons.append("Mood is showing a downward trend.")

    if avg_sleep < 6:
        score += 10
        reasons.append("Average sleep is below 6 hours.")
    elif avg_sleep < 7:
        score += 5
        reasons.append("Average sleep is below 7 hours.")

    if avg_mood < 2.5:
        score += 10
        reasons.append("Average mood is quite low.")
    elif avg_mood < 3.2:
        score += 5
        reasons.append("Average mood is below the mid-point.")

    # Social jetlag — a separate factor from sleep DURATION above. This
    # flags inconsistent sleep TIMING (erratic vs. regular nights), not a
    # fixed "good bedtime" cutoff. See social_jetlag_hours() docstring for
    # the research citation.
    jetlag = social_jetlag_hours(data)
    jetlag_reason = None
    if jetlag is not None and jetlag >= SOCIAL_JETLAG_THRESHOLD_HOURS:
        score += 18
        jetlag_reason = (
            f"Your sleep timing swings by about {jetlag:.1f}h between your steadier "
            "and more irregular nights — research links this level of social jetlag "
            "to higher burnout and depression risk in students."
        )
        reasons.append(jetlag_reason)

    # Purely informational, like the jetlag/percent framing above — a
    # positive counterpart to `reasons`, never a discount on `score`. A
    # good night right after a rough stretch doesn't erase the stretch;
    # it's just worth noticing on its own.
    good_reset_reason = _good_reset(data)

    score = min(score, 100)

    if score >= 60:
        level = "High"
        suggestion = "Reduce today's load by about 25–30%, protect a full night of sleep, and split hard work into shorter blocks."
    elif score >= 30:
        level = "Moderate"
        suggestion = "Switch one heavy study block for a lighter review session and add a real recovery break."
    else:
        level = "Low"
        suggestion = "Keep your current rhythm. Protect sleep and avoid adding study hours just because you have spare time."

    return {
        "score": score,
        "level": level,
        "reasons": reasons[:4],
        "suggestion": suggestion,
        "avg_study": round(avg_study, 1),
        "avg_sleep": round(avg_sleep, 1),
        "avg_mood": round(avg_mood, 1),
        "study_delta": round(study_delta, 1),
        "sleep_delta": round(sleep_delta, 1),
        "mood_delta": round(mood_delta, 1),
        "study_pct": study_pct,
        "social_jetlag": round(jetlag, 1) if jetlag is not None else None,
        "jetlag_reason": jetlag_reason,
        "good_reset": good_reset_reason,
    }


def describe_today(row):
    """
    A short, standalone read of a single day's numbers — independent of
    the 30-day trend above. Used so the dashboard can say "today looked
    good" and "the trend still needs attention" as two separate, honest
    statements instead of one message that reads as if today was bad too.
    """

    sleep = float(row["sleep_hours"])
    mood = float(row["mood"])
    study = float(row["study_hours"])

    if sleep >= 7 and mood >= 4 and study <= 6:
        return {"tone": "good", "text": "Today on its own looks good — solid sleep, decent mood, a reasonable load."}
    if sleep < 6 or mood <= 2 or study >= 8:
        return {"tone": "tough", "text": "Today on its own looks like a tough day."}
    return {"tone": "neutral", "text": "Today on its own looks fairly typical."}

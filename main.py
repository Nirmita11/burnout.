from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo
import hashlib
import os

from typing import List, Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from database import (
    init_db,
    get_user_by_email,
    create_user,
    upsert_log,
    get_logs,
    get_subjects,
    create_subject,
    update_subject,
    delete_subject,
    set_recovery_minutes,
)

from risk import analyze, describe_today, SLEEP_RESEARCH_SOURCES, SOCIAL_JETLAG_THRESHOLD_HOURS
from timetable import generate_weekly_timetable, DAYS
from ml_model import predict_probability, get_model_metrics


# =========================================================
# APP SETUP
# =========================================================

app = FastAPI(
    title="Burnout Detection & Adaptive Study Scheduling"
)

# Serve CSS / static files
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

# Session authentication
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get(
        "SESSION_SECRET",
        "burnout-demo-secret-change-this"
    ),
)

# Jinja templates
templates = Jinja2Templates(
    directory="templates"
)

# Cache-busting query string for /static/app.css so browsers pick up
# CSS edits immediately instead of serving a stale cached copy.
_css_path = Path(__file__).with_name("static") / "app.css"
templates.env.globals["css_version"] = int(_css_path.stat().st_mtime)


# =========================================================
# DATABASE
# =========================================================

init_db()


# =========================================================
# PASSWORD HASHING
# =========================================================

def hash_password(password: str) -> str:
    """
    Hash password using scrypt.
    """

    salt = os.urandom(16)

    hashed = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )

    return f"{salt.hex()}${hashed.hex()}"


def verify_password(
    password: str,
    stored_password: str
) -> bool:
    """
    Verify password against stored scrypt hash.
    """

    try:
        salt_hex, hash_hex = stored_password.split("$")

        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)

        actual_hash = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
        )

        return actual_hash == expected_hash

    except (ValueError, TypeError):
        return False


# =========================================================
# CURRENT USER
# =========================================================

def _user_now(request: Request) -> datetime:
    """The current moment in the visitor's own timezone, not the
    server's. A small script (base.html) sets a `tz` cookie to the
    browser's IANA zone on every page load; this reads it back.

    Deployed on Render, the server's own local time is UTC — a student
    in, say, IST (UTC+5:30) crosses into a new day over five hours
    before the server does. Every "today" in this app (the dashboard
    date/greeting, the log form's default date, which day the weekly
    plan starts on) needs to mean *their* today, not the server's, or
    it visibly disagrees with their own clock for hours around midnight.

    Falls back to the server's local time when the cookie isn't there
    yet (a visitor's very first request in a session) or names a zone
    we don't recognize.
    """
    tz_name = request.cookies.get("tz")
    if tz_name:
        # Starlette reads the Cookie header raw — it does NOT percent-
        # decode values — but the script that sets this cookie encodes
        # it (encodeURIComponent), turning "Asia/Kolkata" into
        # "Asia%2FCalcutta". Left undecoded, ZoneInfo() rejects that
        # string and this silently fell back to server time on every
        # single request, which looked exactly like the cookie wasn't
        # working at all.
        tz_name = unquote(tz_name)
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now()


def time_of_day_greeting(now: datetime) -> str:
    """A quiet contextual greeting — purely presentational."""

    if now.hour < 12:
        return "Good morning"
    if now.hour < 18:
        return "Good afternoon"
    return "Good evening"


def _logging_streak(rows) -> int:
    """
    Consecutive calendar days logged, counting back from the most
    recently logged day. Purely a small encouragement signal for the
    dashboard's log-today section — not used anywhere in risk scoring.
    """

    if not rows:
        return 0

    logged_dates = {date.fromisoformat(r["log_date"]) for r in rows}
    streak = 0
    cursor = max(logged_dates)
    while cursor in logged_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def current_user(request: Request):
    """
    Get currently logged-in user.

    We store the user's email in the session because
    database.py already provides get_user_by_email().
    """

    email = request.session.get("email")

    if not email:
        return None

    return get_user_by_email(email)


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home(request: Request):

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "user": current_user(request),
        },
    )


# =========================================================
# SIGNUP PAGE
# =========================================================

@app.get("/signup")
def signup_page(request: Request):

    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "request": request,
            "mode": "signup",
            "error": None,
        },
    )


# =========================================================
# SIGNUP
# =========================================================

@app.post("/signup")
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):

    email = email.strip().lower()

    # Password validation
    if len(password) < 6:

        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "mode": "signup",
                "error": "Use at least 6 characters.",
            },
            status_code=400,
        )

    # Check existing account
    existing_user = get_user_by_email(email)

    if existing_user:

        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "mode": "signup",
                "error": "That email is already registered.",
            },
            status_code=400,
        )

    # Create user
    password_hash = hash_password(password)

    create_user(
        email,
        password_hash
    )

    # Login immediately
    request.session["email"] = email

    return RedirectResponse(
        "/dashboard",
        status_code=303,
    )


# =========================================================
# LOGIN PAGE
# =========================================================

@app.get("/login")
def login_page(request: Request):

    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "request": request,
            "mode": "login",
            "error": None,
        },
    )


# =========================================================
# LOGIN
# =========================================================

@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):

    email = email.strip().lower()

    user = get_user_by_email(email)

    # Check credentials
    if not user or not verify_password(
        password,
        user["password_hash"]
    ):

        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "request": request,
                "mode": "login",
                "error": "Email or password is incorrect.",
            },
            status_code=401,
        )

    # Save email in session
    request.session["email"] = email

    return RedirectResponse(
        "/dashboard",
        status_code=303,
    )


# =========================================================
# LOGOUT
# =========================================================

@app.get("/logout")
def logout(request: Request):

    request.session.clear()

    return RedirectResponse(
        "/",
        status_code=303,
    )


# =========================================================
# DASHBOARD
# =========================================================

@app.get("/dashboard")
def dashboard(request: Request):

    user = current_user(request)

    # Not logged in
    if not user:

        return RedirectResponse(
            "/login",
            status_code=303,
        )

    now = _user_now(request)
    today = now.date()

    # The rule-based engine looks at up to the last 30 days. This is also
    # what drives the dashboard's chart/history/streak below, so that
    # section isn't pinned at a fixed "14" either — a student with 9 days
    # of history sees a 9-day history, and it grows day by day (10, 11,
    # 12, ...) until it reaches the 30-day cap, rather than silently
    # staying labeled "14 days" forever regardless of what's really shown.
    db_rows = get_logs(
        user["id"],
        30
    )

    # Convert sqlite3.Row → normal dictionaries
    # so Jinja |tojson works correctly.
    rows = [
        dict(row)
        for row in reversed(db_rows)
    ]

    # Burnout analysis
    result = analyze(rows)

    # Per-day signal for the history table — the real rule-based engine,
    # run on the data available up to and including that day.
    for i, row in enumerate(rows):
        row["day_risk"] = analyze(rows[: i + 1])["level"]

    # Weekly timetable suggestion. `subjects` — straight from the same
    # table backing "Manage subjects & commitments" — is the ONLY source
    # of schedule data; if it's empty, the plan comes back empty and the
    # dashboard shows a real "add your first subject" state (see
    # timetable.py's module docstring for why that matters). Recovery
    # break length uses the student's own stated preference as the
    # baseline when they've set one. The day starts at their own logged
    # wake-up time when they've given one, else falls back to 9:00.
    subjects = [dict(s) for s in get_subjects(user["id"])]
    latest_wake_time = rows[-1].get("wake_time") if rows else None
    weekly_plan = generate_weekly_timetable(
        result.get("level", "Low"), subjects, user["recovery_minutes"], latest_wake_time, today=today
    )

    # "Today's Schedule" is just day 0 of the same real plan above — not a
    # separate hardcoded-by-risk-level template.
    today_schedule = weekly_plan["days"][0] if weekly_plan.get("days") else None

    # A single day's numbers read on their own, separate from the 30-day
    # trend above — so a good day never gets buried in a trend-level
    # "needs attention" message.
    today_desc = describe_today(rows[-1]) if rows else None

    logged_today = any(r["log_date"] == today.isoformat() for r in rows)

    # Secondary ML signal, shown only as a small corner badge next to the
    # main risk card (see dashboard.html) — never its own competing block.
    # The full breakdown still lives on its own /model-insights page.
    ml_probability = None
    if rows:
        latest = rows[-1]
        ml_probability = predict_probability(
            latest["study_hours"], latest["sleep_hours"], latest["mood"]
        )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "risk": result,
            "today_desc": today_desc,
            "today": today.isoformat(),
            "today_label": today.strftime("%A, %B ") + str(today.day),
            "greeting": time_of_day_greeting(now),
            "has_data": bool(rows),
            "logged_today": logged_today,
            "streak": _logging_streak(rows),
            "weekly_plan": weekly_plan,
            "today_schedule": today_schedule,
            "subjects": subjects,
            "sleep_sources": SLEEP_RESEARCH_SOURCES,
            "jetlag_threshold": SOCIAL_JETLAG_THRESHOLD_HOURS,
            "ml_probability": ml_probability,
        },
    )


# =========================================================
# MODEL INSIGHTS — secondary ML signal, its own page
# =========================================================

@app.get("/model-insights")
def model_insights(request: Request):

    user = current_user(request)

    if not user:
        return RedirectResponse("/login", status_code=303)

    db_rows = get_logs(user["id"], 1)
    latest = dict(db_rows[0]) if db_rows else None

    ml_probability = None
    if latest:
        ml_probability = predict_probability(
            latest["study_hours"], latest["sleep_hours"], latest["mood"]
        )

    return templates.TemplateResponse(
        request,
        "model_insights.html",
        {
            "request": request,
            "user": user,
            "latest": latest,
            "ml_probability": ml_probability,
            "metrics": get_model_metrics(),
        },
    )


# =========================================================
# DAILY LOG
# =========================================================

def _hours_between(sleep_start: str, wake_time: str) -> float:
    """
    Hours slept between a bedtime and a wake-up clock time, handling the
    normal overnight wrap (bedtime after midnight is earlier than wake
    time isn't possible for a real night's sleep, so if wake <= start we
    assume wake happened the next calendar day).
    """

    sh, sm = (int(x) for x in sleep_start.split(":"))
    wh, wm = (int(x) for x in wake_time.split(":"))
    start_m = sh * 60 + sm
    wake_m = wh * 60 + wm
    if wake_m <= start_m:
        wake_m += 24 * 60
    return round((wake_m - start_m) / 60.0, 2)


@app.post("/log")
def log_day(
    request: Request,
    log_date: str = Form(...),
    study_hours: float = Form(...),
    mood: int = Form(...),
    sleep_start: Optional[str] = Form(None),
    wake_time: Optional[str] = Form(None),
    sleep_hours: Optional[float] = Form(None),
):

    user = current_user(request)

    # Login required
    if not user:

        return RedirectResponse(
            "/login",
            status_code=303,
        )

    # -----------------------------------------------------
    # VALIDATION
    # -----------------------------------------------------

    # Study hours
    study_hours = max(
        0,
        min(24, study_hours)
    )

    # Sleep hours: calculated from bedtime -> wake-up time whenever both
    # are given (the normal path — see the log form). Falls back to a
    # direct hours value only if a caller doesn't supply clock times, so
    # no existing data-entry capability is lost.
    if sleep_start and wake_time:
        sleep_hours = _hours_between(sleep_start, wake_time)
    elif sleep_hours is None:
        sleep_hours = 0
    sleep_hours = max(0, min(24, sleep_hours))

    # Mood 1–5
    mood = max(
        1,
        min(5, mood)
    )

    # -----------------------------------------------------
    # INSERT / UPDATE
    # -----------------------------------------------------

    upsert_log(
        user_id=user["id"],
        log_date=log_date,
        study_hours=study_hours,
        sleep_hours=sleep_hours,
        mood=mood,
        sleep_start=sleep_start or None,
        wake_time=wake_time or None,
    )

    return RedirectResponse(
        "/dashboard?saved=1",
        status_code=303,
    )


# =========================================================
# WEEKLY TIMETABLE — SUBJECTS
# =========================================================

def _clean_preferred_days(raw_days):
    """Turns the checked-box values from the subject form into a clean,
    canonically-ordered "Monday,Wednesday,Friday" string (or None), so
    timetable.py can trust it without re-validating. Dedupes and drops
    anything that isn't one of the real weekday names.
    """
    if not raw_days:
        return None
    chosen = {d for d in raw_days if d in DAYS}
    if not chosen:
        return None
    return ",".join(d for d in DAYS if d in chosen)


@app.post("/timetable/subjects")
def add_subject(
    request: Request,
    name: str = Form(...),
    estimated_hours: float = Form(...),
    deadline_date: Optional[str] = Form(None),
    is_fixed: Optional[str] = Form(None),
    fixed_time: Optional[str] = Form(None),
    preferred_days: List[str] = Form([]),
    preferred_time: Optional[str] = Form(None),
):

    user = current_user(request)

    if not user:
        return RedirectResponse("/login", status_code=303)

    name = name.strip()
    if not name:
        return RedirectResponse("/dashboard#subjectManager", status_code=303)

    estimated_hours = max(0.25, min(40, estimated_hours))
    fixed = bool(is_fixed)

    create_subject(
        user_id=user["id"],
        name=name,
        estimated_hours=estimated_hours,
        deadline_date=deadline_date or None,
        is_fixed=fixed,
        fixed_day=None,
        fixed_time=fixed_time if fixed else None,
        preferred_days=_clean_preferred_days(preferred_days),
        preferred_time=(preferred_time or None) if not fixed else None,
    )

    return RedirectResponse("/dashboard?updated=1#subjectManager", status_code=303)


@app.post("/timetable/subjects/{subject_id}/edit")
def edit_subject(
    request: Request,
    subject_id: int,
    name: str = Form(...),
    estimated_hours: float = Form(...),
    deadline_date: Optional[str] = Form(None),
    is_fixed: Optional[str] = Form(None),
    fixed_time: Optional[str] = Form(None),
    preferred_days: List[str] = Form([]),
    preferred_time: Optional[str] = Form(None),
):

    user = current_user(request)

    if not user:
        return RedirectResponse("/login", status_code=303)

    name = name.strip()
    if not name:
        return RedirectResponse("/dashboard#subjectManager", status_code=303)

    estimated_hours = max(0.25, min(40, estimated_hours))
    fixed = bool(is_fixed)

    update_subject(
        user_id=user["id"],
        subject_id=subject_id,
        name=name,
        estimated_hours=estimated_hours,
        deadline_date=deadline_date or None,
        is_fixed=fixed,
        fixed_day=None,
        fixed_time=fixed_time if fixed else None,
        preferred_days=_clean_preferred_days(preferred_days),
        preferred_time=(preferred_time or None) if not fixed else None,
    )

    return RedirectResponse("/dashboard?updated=1#subjectManager", status_code=303)


@app.post("/timetable/subjects/{subject_id}/delete")
def remove_subject(request: Request, subject_id: int):

    user = current_user(request)

    if not user:
        return RedirectResponse("/login", status_code=303)

    delete_subject(user["id"], subject_id)

    return RedirectResponse("/dashboard?updated=1#subjectManager", status_code=303)


@app.post("/timetable/subjects/{subject_id}/complete")
def complete_subject(request: Request, subject_id: int):
    """Marks a subject/commitment done. Same effect as removing it — once
    its work is finished, it shouldn't keep taking up slots in future
    days — but framed and flashed separately from the delete button so
    "I finished this" and "I don't want to track this" read as the two
    different actions they are, even though today they do the same thing
    to the row.
    """

    user = current_user(request)

    if not user:
        return RedirectResponse("/login", status_code=303)

    delete_subject(user["id"], subject_id)

    return RedirectResponse("/dashboard?completed=1#subjectManager", status_code=303)


@app.post("/preferences/recovery")
def save_recovery_preference(request: Request, recovery_minutes: Optional[str] = Form(None)):

    user = current_user(request)

    if not user:
        return RedirectResponse("/login", status_code=303)

    # Rounded to the nearest 15 minutes once, here, at save time — so the
    # value shown back to the student and the value the schedule actually
    # uses are always the same number, and every generated block time
    # stays on a clean 15-minute grid.
    minutes = None
    if recovery_minutes and recovery_minutes.strip():
        try:
            raw = int(float(recovery_minutes))
            minutes = max(15, min(90, round(raw / 15) * 15))
        except ValueError:
            minutes = None

    set_recovery_minutes(user["id"], minutes)

    return RedirectResponse("/dashboard?updated=1#subjectManager", status_code=303)


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "app": "Early Burnout Detection and Adaptive Study Scheduling",
    }
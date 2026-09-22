import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

# Set DATABASE_URL (a Postgres connection string, e.g. from Supabase) in
# production; leave it unset locally and this falls straight back to the
# existing SQLite file, so nothing about local dev changes.
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).with_name("burnout.db"))))

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    log_date TEXT NOT NULL,
    study_hours REAL NOT NULL,
    sleep_hours REAL NOT NULL,
    mood INTEGER NOT NULL CHECK(mood BETWEEN 1 AND 5),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, log_date),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    estimated_hours REAL NOT NULL DEFAULT 2,
    deadline_date TEXT,
    is_fixed INTEGER NOT NULL DEFAULT 0,
    fixed_day TEXT,
    fixed_time TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT now()::text
);

CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    log_date TEXT NOT NULL,
    study_hours REAL NOT NULL,
    sleep_hours REAL NOT NULL,
    mood INTEGER NOT NULL CHECK(mood BETWEEN 1 AND 5),
    created_at TEXT DEFAULT now()::text,
    UNIQUE(user_id, log_date),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS subjects (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    estimated_hours REAL NOT NULL DEFAULT 2,
    deadline_date TEXT,
    is_fixed INTEGER NOT NULL DEFAULT 0,
    fixed_day TEXT,
    fixed_time TEXT,
    created_at TEXT DEFAULT now()::text,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


class _PGConn:
    """Thin adapter so the rest of this file can keep writing sqlite-style
    `?` placeholders and dict-like rows against a Postgres connection —
    every function below was written once, against sqlite3's interface,
    and stays that way instead of forking into two copies per backend.
    """

    def __init__(self, conn):
        import psycopg2.extras
        self._conn = conn
        self._extras = psycopg2.extras

    def execute(self, query, params=()):
        cur = self._conn.cursor(cursor_factory=self._extras.RealDictCursor)
        cur.execute(query.replace("?", "%s"), params)
        return cur

    def executescript(self, script):
        cur = self._conn.cursor()
        cur.execute(script)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


@contextmanager
def get_db():
    if USE_POSTGRES:
        import psycopg2
        conn = _PGConn(psycopg2.connect(DATABASE_URL))
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(db, table, column, coltype):
    if USE_POSTGRES:
        cols = [
            r["column_name"] for r in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table,),
            ).fetchall()
        ]
    else:
        cols = [r["name"] for r in db.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    if not USE_POSTGRES:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA_POSTGRES if USE_POSTGRES else SCHEMA_SQLITE)
        # Migration: user-set recovery break length (minutes). Nullable —
        # falls back to the default calculation when not set.
        _ensure_column(db, "users", "recovery_minutes", "INTEGER")
        # Migration: sleep-start / wake-up clock times ("HH:MM"), nullable.
        # sleep_hours is still stored (computed from these two when given)
        # so risk.py and every existing average/chart keep working as-is.
        _ensure_column(db, "logs", "sleep_start", "TEXT")
        _ensure_column(db, "logs", "wake_time", "TEXT")
        _ensure_column(db, "logs", "study_start_time", "TEXT")
        # Migration: student-chosen days ("Monday,Wednesday,Friday"). For
        # a flexible subject, which days to spread its hours across
        # instead of the scheduler deciding evenly on its own — nullable,
        # falls back to the automatic spread when not set. For a fixed
        # commitment, which day(s) it recurs on every week — replaces the
        # older single-value fixed_day column (still read as a fallback
        # for rows saved before this, but no longer written to).
        _ensure_column(db, "subjects", "preferred_days", "TEXT")
        # Migration: optional preferred start time ("HH:MM") for a
        # flexible (deadline-based) subject's work sessions. Nullable —
        # falls back to wherever the scheduler would otherwise place it
        # when not set. Distinct from fixed_time, which is the required
        # time for a fixed commitment.
        _ensure_column(db, "subjects", "preferred_time", "TEXT")


def get_user_by_email(email):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()


def create_user(email, password_hash):
    with get_db() as db:
        if USE_POSTGRES:
            row = db.execute(
                "INSERT INTO users(email, password_hash) VALUES (?, ?) RETURNING id",
                (email.lower().strip(), password_hash),
            ).fetchone()
            return row["id"]
        cur = db.execute(
            "INSERT INTO users(email, password_hash) VALUES (?, ?)",
            (email.lower().strip(), password_hash),
        )
        return cur.lastrowid


def upsert_log(user_id, log_date, study_hours, sleep_hours, mood, sleep_start=None, wake_time=None, study_start_time=None):
    with get_db() as db:
        db.execute("""
            INSERT INTO logs(user_id, log_date, study_hours, sleep_hours, mood, sleep_start, wake_time, study_start_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, log_date) DO UPDATE SET
                study_hours=excluded.study_hours,
                sleep_hours=excluded.sleep_hours,
                mood=excluded.mood,
                sleep_start=excluded.sleep_start,
                wake_time=excluded.wake_time,
                study_start_time=excluded.study_start_time
        """, (user_id, log_date, study_hours, sleep_hours, mood, sleep_start, wake_time, study_start_time))


def get_logs(user_id, limit=14):
    with get_db() as db:
        return db.execute("""
            SELECT log_date, study_hours, sleep_hours, mood, sleep_start, wake_time, study_start_time
            FROM logs
            WHERE user_id = ?
            ORDER BY log_date DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()


def delete_user_logs(user_id):
    with get_db() as db:
        db.execute("DELETE FROM logs WHERE user_id = ?", (user_id,))


def delete_user(email):
    with get_db() as db:
        db.execute("DELETE FROM users WHERE email = ?", (email.lower().strip(),))


def get_subjects(user_id):
    with get_db() as db:
        return db.execute("""
            SELECT * FROM subjects WHERE user_id = ? ORDER BY created_at ASC
        """, (user_id,)).fetchall()


def create_subject(user_id, name, estimated_hours, deadline_date, is_fixed, fixed_day, fixed_time, preferred_days=None, preferred_time=None):
    with get_db() as db:
        db.execute("""
            INSERT INTO subjects(user_id, name, estimated_hours, deadline_date, is_fixed, fixed_day, fixed_time, preferred_days, preferred_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, name, estimated_hours, deadline_date, int(is_fixed), fixed_day, fixed_time, preferred_days, preferred_time))


def delete_subject(user_id, subject_id):
    with get_db() as db:
        db.execute("DELETE FROM subjects WHERE user_id = ? AND id = ?", (user_id, subject_id))


def delete_subjects(user_id):
    with get_db() as db:
        db.execute("DELETE FROM subjects WHERE user_id = ?", (user_id,))


def update_subject(user_id, subject_id, name, estimated_hours, deadline_date, is_fixed, fixed_day, fixed_time, preferred_days=None, preferred_time=None):
    with get_db() as db:
        db.execute("""
            UPDATE subjects
            SET name = ?, estimated_hours = ?, deadline_date = ?, is_fixed = ?, fixed_day = ?, fixed_time = ?, preferred_days = ?, preferred_time = ?
            WHERE user_id = ? AND id = ?
        """, (name, estimated_hours, deadline_date, int(is_fixed), fixed_day, fixed_time, preferred_days, preferred_time, user_id, subject_id))


def set_recovery_minutes(user_id, minutes):
    with get_db() as db:
        db.execute("UPDATE users SET recovery_minutes = ? WHERE id = ?", (minutes, user_id))

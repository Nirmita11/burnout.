from datetime import date, timedelta
import hashlib
import os

from database import (
    init_db,
    get_user_by_email,
    create_user,
    delete_user_logs,
    upsert_log,
    delete_subjects,
    create_subject,
)


def hash_password(password):
    salt = os.urandom(16)

    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )

    return f"{salt.hex()}${digest.hex()}"


def ensure_user(email):
    user = get_user_by_email(email)

    password_hash = hash_password("demo123")

    if user:
        # Reset password for existing demo user
        from database import get_db

        with get_db() as db:
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user["id"]),
            )

        delete_user_logs(user["id"])
        return user["id"]

    return create_user(email, password_hash)


def add_history(uid, study, sleep, mood, sleep_start=None, wake_time=None):
    start = date.today() - timedelta(days=13)

    for i in range(14):
        upsert_log(
            uid,
            (start + timedelta(days=i)).isoformat(),
            study[i],
            sleep[i],
            mood[i],
            sleep_start=sleep_start[i] if sleep_start else None,
            wake_time=wake_time[i] if wake_time else None,
        )


def main():
    init_db()

    steady = ensure_user("steady@demo.com")
    burnout = ensure_user("burnout@demo.com")

    # Calm scenario — consistent bedtime, near-zero social jetlag.
    add_history(
        steady,
        study=[
            3.2, 3.5, 3.0, 3.8, 3.4, 3.6, 3.1,
            3.7, 3.3, 3.5, 3.4, 3.6, 3.2, 3.5
        ],
        sleep=[
            7.6, 7.4, 7.8, 7.2, 7.5, 7.7, 7.3,
            7.6, 7.5, 7.4, 7.7, 7.2, 7.6, 7.5
        ],
        mood=[
            4, 4, 5, 4, 4, 5, 4,
            4, 5, 4, 4, 5, 4, 5
        ],
        sleep_start=[
            "23:00", "23:05", "22:55", "23:10", "23:00", "22:50", "23:05",
            "23:00", "23:05", "23:10", "22:55", "23:10", "23:00", "23:05",
        ],
        wake_time=[
            "06:36", "06:29", "06:43", "06:22", "06:30", "06:32", "06:23",
            "06:36", "06:35", "06:34", "06:37", "06:22", "06:36", "06:35",
        ],
    )

    # Escalating scenario — bedtime drifts from 23:00 to 03:00 while wake
    # time stays pinned near 7am (class/commitments), a textbook social
    # jetlag pattern. This should trip the new social-jetlag risk factor.
    add_history(
        burnout,
        study=[
            3.0, 3.2, 3.5, 3.8, 4.0, 4.2, 4.5,
            5.0, 6.0, 6.2, 6.5, 7.0, 7.5, 8.0
        ],
        sleep=[
            8.0, 7.8, 7.6, 7.4, 7.2, 7.0, 6.8,
            6.5, 6.0, 5.8, 5.5, 5.2, 4.8, 4.5
        ],
        mood=[
            5, 5, 5, 4, 4, 4, 4,
            3, 3, 3, 2, 2, 1, 1
        ],
        sleep_start=[
            "23:00", "23:10", "23:20", "23:30", "23:45", "00:00", "00:15",
            "00:45", "01:15", "01:30", "02:00", "02:15", "02:30", "03:00",
        ],
        wake_time=[
            "07:00", "06:58", "06:56", "06:54", "06:57", "07:00", "07:03",
            "07:15", "07:15", "07:18", "07:30", "07:27", "07:18", "07:30",
        ],
    )

    # Moderate scenario: early, mild signs of the pattern — not severe.
    # Bedtime drifts a little (23:00 -> 00:25), a smaller shift than the
    # High-risk account's, matching "early signs, not yet severe".
    stretched = ensure_user("stretched@demo.com")
    add_history(
        stretched,
        study=[
            3.3, 3.5, 3.4, 3.6, 3.5, 3.7, 3.6,
            4.3, 4.5, 4.6, 4.8, 5.0, 5.1, 5.2
        ],
        sleep=[
            7.6, 7.5, 7.7, 7.4, 7.6, 7.5, 7.6,
            6.9, 6.8, 6.7, 6.6, 6.5, 6.6, 6.5
        ],
        mood=[
            4, 4, 4, 4, 4, 5, 4,
            3, 3, 4, 3, 3, 4, 3
        ],
        sleep_start=[
            "23:00", "23:00", "22:50", "23:05", "23:00", "23:10", "22:55",
            "23:30", "23:45", "00:00", "00:10", "00:20", "00:10", "00:25",
        ],
        wake_time=[
            "06:36", "06:30", "06:32", "06:29", "06:36", "06:40", "06:31",
            "06:24", "06:33", "06:42", "06:46", "06:50", "06:46", "06:55",
        ],
    )

    # Every demo account gets REAL subjects/commitments — inserted as
    # actual rows in the same `subjects` table the "Manage subjects &
    # commitments" panel reads from, so opening that panel on ANY of
    # these accounts immediately shows a populated, matching list — never
    # an illustrative stand-in disconnected from what generated the week.
    today = date.today()

    # Kept deliberately light: this is a Low-risk "before" demo account,
    # and a heavier subject load makes the generated week look packed
    # regardless of risk level — the opposite of what a healthy-looking
    # week should demonstrate.
    delete_subjects(steady)
    create_subject(
        user_id=steady, name="Problem Set 4", estimated_hours=1.5,
        deadline_date=(today + timedelta(days=5)).isoformat(),
        is_fixed=False, fixed_day=None, fixed_time=None,
    )
    create_subject(
        user_id=steady, name="Weekly Seminar", estimated_hours=1,
        deadline_date=None, is_fixed=True, fixed_day="Tuesday", fixed_time="10:00",
    )

    delete_subjects(burnout)
    create_subject(
        user_id=burnout, name="Thesis Draft", estimated_hours=8,
        deadline_date=(today + timedelta(days=2)).isoformat(),
        is_fixed=False, fixed_day=None, fixed_time=None,
    )
    create_subject(
        user_id=burnout, name="Midterm Prep", estimated_hours=6,
        deadline_date=(today + timedelta(days=4)).isoformat(),
        is_fixed=False, fixed_day=None, fixed_time=None,
    )
    create_subject(
        user_id=burnout, name="Lab Report", estimated_hours=5,
        deadline_date=(today + timedelta(days=6)).isoformat(),
        is_fixed=False, fixed_day=None, fixed_time=None,
    )
    create_subject(
        user_id=burnout, name="Group Project Meeting", estimated_hours=2,
        deadline_date=None, is_fixed=True, fixed_day="Thursday", fixed_time="15:00",
    )

    # Same reasoning as steady: Moderate here means "early signs", not
    # "already overloaded" — kept light so the week still reads as normal.
    delete_subjects(stretched)
    create_subject(
        user_id=stretched, name="Research Paper", estimated_hours=1,
        deadline_date=(today + timedelta(days=5)).isoformat(),
        is_fixed=False, fixed_day=None, fixed_time=None,
    )
    create_subject(
        user_id=stretched, name="Discussion Section", estimated_hours=1,
        deadline_date=None, is_fixed=True, fixed_day="Monday", fixed_time="11:00",
    )

    print("Demo data seeded successfully.")
    print()
    print("Steady account:")
    print("Email: steady@demo.com")
    print("Password: demo123")
    print()
    print("Burnout account:")
    print("Email: burnout@demo.com")
    print("Password: demo123")
    print()
    print("Stretched account (Moderate risk):")
    print("Email: stretched@demo.com")
    print("Password: demo123")


if __name__ == "__main__":
    main()
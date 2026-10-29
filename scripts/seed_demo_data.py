"""Creates CLEARLY LABELLED demo data through the real services (so every rule and audit entry applies).

    python -m scripts.seed_demo_data --students 300 [--password 'Some-Strong-Pass1']

It refuses to run if the database already contains users or students, so it can never mix with real records.
After seeding a demo database, set SIS_DEMO_MODE=1 so every page shows a DEMO banner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import secrets
import sys

from sqlalchemy import func, select

from config.settings import settings, today
from database.connection import session_scope
from database.migrate import run_migrations
from database.models import Student, User
from services import academic_service as ac
from services import attendance_service as att
from services import auth_service as auth
from services import grade_service as gs
from services import promotion_service as ps
from services import student_service as ss

FIRST = ["Aarav", "Diya", "Vihaan", "Anaya", "Aditya", "Ishita", "Reyansh", "Kavya", "Arjun", "Saanvi", "Krishna", "Meera",
         "Rohan", "Nisha", "Karan", "Pooja", "Dev", "Riya", "Yash", "Tanvi", "Manav", "Sneha", "Harsh", "Aditi"]
LAST = ["Sharma", "Verma", "Patel", "Singh", "Gupta", "Joshi", "Mishra", "Chouhan", "Yadav", "Jain", "Rathore", "Tiwari"]
SUBJECTS = [("ENG", "English"), ("MATH", "Mathematics"), ("SCI", "Science"), ("SST", "Social Studies"), ("CS", "Computer Science")]


def weekdays_back(end: dt.date, count: int, not_before: dt.date) -> list[dt.date]:
    days, d = [], end
    while len(days) < count and d >= not_before:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return sorted(days)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--students", type=int, default=240)
    parser.add_argument("--password", default=None, help="password for every demo account (default: random, printed)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    run_migrations()
    with session_scope() as s:
        if s.scalar(select(func.count(User.id))) or s.scalar(select(func.count(Student.id))):
            sys.exit("Refusing to seed: this database already has users or students. Use a fresh database for demo data.")
    rnd = random.Random(args.seed)
    password = args.password or secrets.token_urlsafe(10) + "aA1"
    auth.bootstrap_admin("demo_admin", "demo_admin@example.org", "Demo Administrator", password)
    admin = auth.authenticate("demo_admin", password)
    auth.create_user(admin, "demo_principal", "Demo Principal", "principal", password)
    teachers = []
    for i in range(1, 7):
        auth.create_user(admin, f"demo_teacher{i}", f"Demo Teacher {i}", "teacher", password, employee_no=f"DT{i}")
        teachers.append(auth.authenticate(f"demo_teacher{i}", password))

    t = today()
    cy_start = t - dt.timedelta(days=120)
    py_start, py_end = cy_start - dt.timedelta(days=365), cy_start - dt.timedelta(days=1)
    label = lambda d: f"{d.year}-{(d.year + 1) % 100:02d}"  # noqa: E731
    py = ac.create_year(admin, label(py_start), py_start, py_end)
    cy = ac.create_year(admin, label(cy_start), cy_start, cy_start + dt.timedelta(days=364), make_current=True)
    classes = {lvl: ac.create_class(admin, f"Class {lvl}", lvl) for lvl in range(6, 11)}
    sections = {n: ac.create_section(admin, n) for n in ("A", "B")}
    subjects = {code: ac.create_subject(admin, code, name) for code, name in SUBJECTS}
    max_level = max(classes)

    # --- previous year: students, attendance, a published annual exam ---
    pairs = [(lvl, sec) for lvl in classes for sec in sections]
    rolls: dict[tuple, int] = {}
    per_pair = max(2, args.students // len(pairs))

    def next_roll(year: int, class_id: int, section_id: int) -> int:
        rolls[(year, class_id, section_id)] = rolls.get((year, class_id, section_id), 0) + 1
        return rolls[(year, class_id, section_id)]

    n = 0
    for lvl, sec in pairs:
        for _ in range(per_pair):
            n += 1
            name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
            ss.create_student(admin, {"full_name": name, "date_of_birth": dt.date(2025 - lvl - 5, rnd.randint(1, 12), rnd.randint(1, 28)),
                                      "admission_date": dt.date(2018, 4, 1), "gender": rnd.choice(["Male", "Female"]),
                                      "guardian_name": f"{rnd.choice(FIRST)} {name.split()[-1]}",
                                      "guardian_phone": f"98{rnd.randint(10000000, 99999999)}"},
                              py, classes[lvl], sections[sec], next_roll(py, classes[lvl], sections[sec]))

    def fill_exam(year: int, term: str, name: str, publish: bool, gap: float) -> None:
        exam = gs.create_examination(admin, year, term, name)
        for lvl, cid in classes.items():
            for code, sid in subjects.items():
                es = gs.add_exam_subject(admin, exam, sid, cid, 100, 35)
                for sec_id in sections.values():
                    roster = gs.marks_roster(admin, es, sec_id)
                    entries = []
                    for r in roster:
                        roll = rnd.random()
                        if roll < gap:
                            entries.append(gs.MarkEntry(r["enrollment_id"], None))          # missing: not yet entered
                        elif roll < gap + 0.02:
                            entries.append(gs.MarkEntry(r["enrollment_id"], None, True))    # absent
                        else:
                            entries.append(gs.MarkEntry(r["enrollment_id"], round(min(100, max(5, rnd.gauss(66, 15))), 1)))
                    if entries:
                        gs.enter_marks(admin, es, entries)
        if publish:
            gs.publish_results(admin, exam, allow_incomplete=True)

    fill_exam(py, "Annual", "Annual Examination", True, 0.0)
    for d in weekdays_back(py_end, 25, py_start):
        for lvl, sec in pairs:
            roster = att.roster(admin, py, classes[lvl], sections[sec], d)
            att.mark_attendance(admin, py, classes[lvl], sections[sec], d,
                                {r["enrollment_id"]: rnd.choices("PALE", [88, 6, 4, 2])[0] for r in roster})

    # --- promotion into the current year, using the real workflow ---
    for lvl in classes:
        rows = ps.eligible_students(admin, py, cy, classes[lvl])
        decisions = []
        for r in rows:
            outcome = "transferred" if rnd.random() < 0.02 else r["suggested"]
            if outcome in ("promoted", "retained"):
                target = classes[lvl + 1] if outcome == "promoted" else classes[lvl]
                decisions.append(ps.Decision(r["enrollment_id"], outcome, target, r["section_id"],
                                             next_roll(cy, target, r["section_id"])))
            else:
                decisions.append(ps.Decision(r["enrollment_id"], outcome))
        if decisions:
            ps.apply_promotion(admin, py, cy, decisions)
    for _ in range(max(6, args.students // 10)):  # new admissions straight into the lowest class
        sec = rnd.choice(list(sections.values()))
        name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        ss.create_student(admin, {"full_name": name, "date_of_birth": dt.date(2014, 6, 1), "admission_date": cy_start,
                                  "guardian_name": f"{rnd.choice(FIRST)} {name.split()[-1]}"},
                          cy, classes[6], sec, next_roll(cy, classes[6], sec))

    # --- current year: teachers, one published unit test, one draft mid-term with gaps, attendance ---
    for i, (lvl, sec) in enumerate(pairs):
        tch = teachers[i % len(teachers)]
        ac.assign_teacher(admin, tch.teacher_id, cy, classes[lvl], sections[sec], None, True)
    fill_exam(cy, "Term 1", "Unit Test 1", True, 0.0)
    fill_exam(cy, "Term 1", "Mid-term", False, 0.2)
    for d in weekdays_back(t, 40, cy_start):
        for lvl, sec in pairs:
            roster = att.roster(admin, cy, classes[lvl], sections[sec], d)
            if roster:
                att.mark_attendance(admin, cy, classes[lvl], sections[sec], d,
                                    {r["enrollment_id"]: rnd.choices("PALE", [88, 6, 4, 2])[0] for r in roster})

    # --- demo logins for the student and parent views ---
    rows, _ = ss.search_students(admin, year_id=cy, page_size=3)
    for i, r in enumerate(rows, start=1):
        uid = auth.create_user(admin, f"demo_student{i}", f"Demo Student {i}", "student", password)
        auth.link_student_account(admin, r["id"], uid)
    pid = auth.create_user(admin, "demo_parent1", "Demo Parent 1", "parent", password)
    for r in rows[:2]:
        auth.link_parent(admin, pid, r["id"])

    with session_scope() as s:
        total = s.scalar(select(func.count(Student.id)))
    print(f"\nDEMO DATA CREATED  ({total} students, years {label(py_start)} and {label(cy_start)})")
    print("Set SIS_DEMO_MODE=1 so the app shows a DEMO banner.\n")
    print("Accounts (all use the same password):")
    print(f"  password : {password}")
    print("  logins   : demo_admin, demo_principal, demo_teacher1..6, demo_student1..3, demo_parent1")
    print(f"  database : {settings.database_url.split('@')[-1]}")


if __name__ == "__main__":
    main()

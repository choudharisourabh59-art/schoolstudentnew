# Scholar SIS - School Information Management System

Streamlit + SQLAlchemy + Plotly application for a school of up to 15,000 students: students, teachers, attendance,
examinations and results, yearly class promotion, per-student portfolios, documents, reports, audit log and backups.
Entry point: **`app.py`** (`streamlit run app.py`).

> **Verification status (read this first).** The code was written and statically cross-checked (every internal import,
> function name and call signature resolves; all files compile). It has **not been executed**: the environment it was
> written in had no network, so Streamlit/SQLAlchemy/pytest could not be installed. Before any real use, run the commands in
> *Testing* below, fix whatever they report, and walk through the acceptance checklist at the end.

## Features
- Login with username or e-mail, Argon2 password hashing, failed-login lockout (5 tries -> 15 min), idle sign-out (30 min),
  account activation/deactivation, admin-issued one-time password-reset codes, change password.
- Roles: administrator, principal, teacher, student, parent. **Every service function checks permission itself**; hiding
  menu items is only cosmetic. Students see only themselves, parents only admin-verified linked children, teachers only
  their assigned classes. (Accountant/fee management is **not** implemented.)
- Students: add, view, edit, search, filter (year/class/section), archive/restore (never deleted), CSV bulk import
  (all-or-nothing, every problem reported), awards, teacher remarks, photo.
- Attendance: P/A/L/E per class and date, no duplicates (DB unique key), no future dates, teacher edit window, audit of
  every edit, daily/monthly/student/class reports, trends, most-absent students. Excused days are left out of the rate.
- Examinations: terms, exams, subjects with max/pass marks, mark entry with validation (no negative, no above max, no
  duplicates, blank != zero, absent flag), `Decimal` arithmetic, configurable grade bands and pass rules, publishing,
  amendment of published results (admin + reason), audit of old/new values. Results are **derived** from raw marks on every
  read, so a changed mark is reflected everywhere immediately.
- Promotion: explicit per-student decision (promoted / retained / transferred / graduated / withdrawn), preview, one
  transaction, duplicate protection, history preserved (one enrollment row per student per year).
- Portfolio per student (profile, attendance, subject marks, performance chart, history, documents, awards, remarks).
- Documents: PDF/JPG/PNG/DOCX/XLSX; content sniffed, size-limited, random storage names, no overwrite, permission-checked
  downloads with checksum verification, soft delete.
- Reports with CSV / Excel / PDF export (spreadsheet-formula injection neutralised).
- Dashboard styled after the reference screenshot, real database queries only, filters for year/class/section/term/subject.
- Audit log (append-only: ORM guard **and** database triggers), backups with checksum + integrity verification.

## Folder structure
```
app.py                      entry point (login, first-run setup)
config/settings.py          environment/secrets configuration, default academic policy
database/                   connection.py, models.py, migrate.py, migrations/ (ordered, versioned)
services/                   all business logic + permission checks (auth, student, attendance, grade, promotion,
                            upload, report, backup, academic, permissions, audit)
pages/                      1_Admin_Dashboard ... 11_Promotion (Streamlit multipage)
components/                 theme (CSS), sidebar, dashboard_cards, charts (Plotly), tables, forms
scripts/seed_demo_data.py   clearly-labelled demo data, built through the real services
tests/                      pytest suites (auth, students, attendance, grades, promotion, uploads, reports)
storage/                    local uploads / dev database (git-ignored)
```

## Installation
**Linux / macOS**
```bash
cd school_information_system
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # edit it; leave DATABASE_URL commented out to use local SQLite
python -m database.migrate      # creates the tables (the app also does this at start-up)
streamlit run app.py
```
**Windows PowerShell**
```powershell
cd school_information_system
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env     # edit it; leave DATABASE_URL out to use local SQLite
python -m database.migrate
streamlit run app.py
```
Python 3.11+ recommended (developed against 3.12).

## Configuration
Environment variables (or Streamlit secrets): see `.env.example`. Key ones: `DATABASE_URL` (PostgreSQL for production;
SQLite file `storage/sis_dev.db` if unset), `STORAGE_BACKEND` (`local` | `db` | `s3`), `MAX_UPLOAD_MB`, `TIMEZONE`.
Academic rules (pass mark, grade bands, attendance rate, promotion thresholds, edit windows) live in the database and are
edited in **Settings > Grading policy** - nothing is hard-coded to one school's rules.

## Database and migrations
`database/migrate.py` applies the ordered migrations in `database/migrations/` and records them in `schema_migrations`.
To change the schema later: add `v0003_<name>.py` with `upgrade(conn)` and append it to `MIGRATIONS`, then run
`python -m database.migrate`. (This is a small built-in runner; adopt Alembic if you prefer autogeneration.)

## First administrator
Start the app on an empty database: a setup form creates the administrator (password >= 10 characters, not a common
password). Alternatively set `SIS_ADMIN_USERNAME` / `SIS_ADMIN_PASSWORD` for an unattended first start. There is no default
password. Then: Settings > Academic structure (year, classes with levels, sections, subjects) -> Settings > Users ->
Teachers (assign classes) -> Students.

## Uploading student records (CSV)
Students > Import CSV. UTF-8, header row, columns: `full_name, date_of_birth, admission_date, class, section` (required) and
`roll_no, gender, guardian_name, guardian_phone, phone, email, address, emergency_contact_name, emergency_contact_phone,
student_code` (optional). Dates `YYYY-MM-DD` or `DD/MM/YYYY`. Class/section names must already exist. If any row is wrong,
nothing is imported and each problem is listed with its row number. Up to 20,000 rows per import.

## Demo data
```bash
SIS_DEMO_MODE=1 DATABASE_URL=sqlite:///storage/demo.db python -m scripts.seed_demo_data --students 300
```
(PowerShell: `$env:SIS_DEMO_MODE=1; $env:DATABASE_URL="sqlite:///storage/demo.db"; python -m scripts.seed_demo_data --students 300`.)
It refuses to run on a database that already has users/students. With `SIS_DEMO_MODE=1` every page shows a DEMO banner.

## Testing
```bash
python -m compileall .
pytest
```
Tests cover authentication and lockout, permission checks, student creation and duplicate IDs, attendance rules and
percentages, invalid marks, Decimal result calculation, publishing/amending, promotion (history, duplicates, rollback on
failure), upload validation/persistence/unauthorised download, report correctness, exports and backup verification.
**Do not rely on this project until `pytest` passes on your machine.**

## Backup and recovery
- **What to back up:** the database (and uploaded files if `STORAGE_BACKEND=local`; with `db` they are inside the database;
  with `s3` use bucket versioning).
- **In the app:** Settings > Backups and storage creates a SQLite copy (consistent online backup) or a `pg_dump` custom
  dump (needs `pg_dump` installed), each with a SHA-256 manifest, and verifies integrity. "Check documents vs storage"
  finds missing and orphaned files.
- **Schedule it:** Linux cron, e.g. `0 2 * * * pg_dump --format=custom -h HOST -U USER -d DB -f /backups/sis_$(date +\%F).dump`.
  Windows: Task Scheduler running the same command. Copy backups **off the server**. Managed PostgreSQL (Neon, Supabase,
  RDS...) also offers point-in-time backups: turn them on and check the retention period.
- **Restore (always rehearse on a test copy first):**
  SQLite: `restore_sqlite_to_test()` or copy the `.sqlite` backup to a new path and point `DATABASE_URL` at it.
  PostgreSQL: `createdb sis_restore_test && pg_restore --no-owner -d sis_restore_test sis_YYYY-MM-DD.dump`, start the app against
  it, log in, open a few students/results/documents. Only then restore to production.
- A backup is only trusted after a successful test restore. Do this at least once a term.

## Deployment
**Streamlit Community Cloud** (as in your screenshot): repository root contains `app.py`; **Main file path = `app.py`**;
Advanced settings > Secrets: paste the values from `.streamlit/secrets.toml.example` (use an external PostgreSQL such as
Neon/Supabase, and `STORAGE_BACKEND = "db"` because the Cloud disk is wiped on restart). Keep the repository **private**.
For a school with real student data, prefer a server you control or a paid host with a Data Processing agreement, run the app
behind HTTPS (Cloud provides it; otherwise nginx/Caddy), and review local privacy law (e.g. India's DPDP Act) for what you store.
Production checklist: strong unique DB password, TLS to the database (`sslmode=require`), no secrets in git, backups scheduled and
tested, administrators limited, passwords changed from any temporary ones.

## Load-testing plan (do before go-live)
15,000 *records* is not 15,000 *concurrent users*. Realistic peak: perhaps 150-400 simultaneous sessions (a morning attendance
rush by ~60 teachers; results day with parents/students; a handful of administrators on dashboards).
1. Seed a throw-away PostgreSQL with 15,000 students, 3 years of attendance (~3-4 million rows) and 3 exams x 8 subjects.
2. Scenarios: (a) 60 teachers marking attendance within 15 minutes; (b) 300 students/parents opening portfolios + results at once;
   (c) 10 admins on the dashboard with filters; (d) 1 admin CSV import of 2,000 rows; (e) nightly backup running during (a).
3. Tooling: `locust`/`k6` against Streamlit's websocket is awkward - instead (i) time service calls directly with a Python
   thread pool (measure p50/p95/p99 per function, watch `pg_stat_statements` and connection count), and (ii) smoke-test the UI with
   Playwright at 20-50 browsers. Streamlit keeps one Python thread per session, so also watch RAM per session.
4. Targets: p95 service call < 500 ms, dashboard < 2 s, no DB connection errors, memory stable over 1 hour.
5. Remedies if it fails: more server RAM/CPU, several app replicas behind a load balancer, PgBouncer, read replica for reports,
   pre-aggregated attendance summaries. Index coverage is already in the schema (`enrollment`, `attendance(date,status)`, names).

## Troubleshooting
- `DATABASE_URL` errors on Streamlit Cloud: it must start with `postgresql://` and end with `?sslmode=require`.
- "Select statement ... correlate" / SQL errors in `grade_service.students_with_missing_marks` or `permissions.py`: these use
  correlated subqueries and were written without being run - send the full traceback.
- Uploads vanish after a restart on Streamlit Cloud: use `STORAGE_BACKEND=db`.
- Locked out: another administrator issues a reset code (Settings > Users); if none exists, reset directly in the database.
- Sidebar menu not highlighted: Streamlit changed its test-ids; only cosmetic (see `components/theme.py`).

## Known limitations (honest list)
No fee/accountant module; no e-mail/SMS (reset codes are handed over by an administrator); `Notification` table exists but no
feature uses it yet; no light/dark switch; roles are a validated column rather than a separate table; the S3 backend and
`pg_dump` backups are written but untested; teacher access is per class (optionally per subject for marks), not per period;
photos are uploaded via the Documents page (category "photo"); the Excel/PDF exports are simple tables, not letterhead report cards.

## Acceptance checklist
`streamlit run app.py` starts - first-run setup works - login/logout - role pages enforce access - students created and found -
attendance saved and rates correct - teacher enters marks - results match the policy - portfolio shows the right student -
promotion keeps history - uploads survive a restart - dashboard charts match the data - reports export - backup restored on a
test copy - `pytest` green.

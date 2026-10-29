"""Run with:  python -m database.migrate   (the app also runs it automatically at start-up)."""
from __future__ import annotations

from sqlalchemy import text

from database.connection import get_engine
from database.migrations import MIGRATIONS


def run_migrations(engine=None) -> list[str]:
    engine = engine or get_engine()
    applied_now: list[str] = []
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(100) PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        done = {row[0] for row in conn.exec_driver_sql("SELECT version FROM schema_migrations")}
        for version, upgrade in MIGRATIONS:
            if version not in done:
                upgrade(conn)
                conn.execute(text("INSERT INTO schema_migrations(version) VALUES (:v)"), {"v": version})
                applied_now.append(version)
    return applied_now


if __name__ == "__main__":
    print("Applied:", run_migrations() or "nothing (already up to date)")

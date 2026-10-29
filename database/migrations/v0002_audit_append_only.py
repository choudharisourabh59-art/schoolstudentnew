"""Database-level protection: audit_logs rows cannot be updated or deleted, even by raw SQL."""


def upgrade(conn) -> None:
    name = conn.dialect.name
    if name == "sqlite":
        for op in ("UPDATE", "DELETE"):
            conn.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS audit_no_{op.lower()} BEFORE {op} ON audit_logs "
                "BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;")
    elif name == "postgresql":
        conn.exec_driver_sql(
            "CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END; $$ LANGUAGE plpgsql;")
        conn.exec_driver_sql("DROP TRIGGER IF EXISTS audit_logs_no_change ON audit_logs;")
        conn.exec_driver_sql(
            "CREATE TRIGGER audit_logs_no_change BEFORE UPDATE OR DELETE ON audit_logs "
            "FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();")

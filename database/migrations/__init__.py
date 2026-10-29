"""Ordered schema migrations. To change the schema later, add v0003_<name>.py with an upgrade(conn)
function and append it to MIGRATIONS. Applied versions are recorded in the schema_migrations table."""
from . import v0001_initial, v0002_audit_append_only

MIGRATIONS = [
    ("0001_initial", v0001_initial.upgrade),
    ("0002_audit_append_only", v0002_audit_append_only.upgrade),
]

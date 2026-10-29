from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from database.models import AuditLog


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return value


def log(session: Session, actor, action: str, record_type: str, record_id: Any = "",
        previous: Any = None, new: Any = None) -> None:
    """Add an audit row inside the caller's transaction (it commits or rolls back with the change)."""
    session.add(AuditLog(user_id=getattr(actor, "id", None), action=action, record_type=record_type,
                         record_id=str(record_id), previous_value=jsonable(previous), new_value=jsonable(new)))

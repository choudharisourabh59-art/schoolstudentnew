"""Engine and session management (SQLite for local, PostgreSQL for production)."""
from __future__ import annotations

import warnings
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config.settings import settings

warnings.filterwarnings("ignore", message=".*does \\*not\\* support Decimal.*", category=SAWarning)

_engine = None
_SessionFactory: sessionmaker[Session] | None = None


def configure(url: str | None = None):
    """(Re)create the engine. Tests call configure('sqlite://') for an isolated in-memory DB."""
    global _engine, _SessionFactory
    url = url or settings.database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        db_file = make_url(url).database
        if db_file and db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)  # the folder may not exist on a fresh server
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=10, pool_recycle=1800,
                      connect_args={"connect_timeout": 10})  # fail fast instead of hanging
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection, _record):  # pragma: no cover - trivial
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    _engine = engine
    _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine


def get_engine():
    if _engine is None:
        configure()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction per call: commit on success, roll back everything on any error."""
    if _SessionFactory is None:
        configure()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

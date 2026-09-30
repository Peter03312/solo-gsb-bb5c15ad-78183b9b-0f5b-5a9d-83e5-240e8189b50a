"""Database engine, startup readiness wait and schema initialisation."""
from __future__ import annotations

import logging
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from .config import settings

log = logging.getLogger("radio.db")

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def wait_for_database(timeout_seconds: float = 60.0) -> None:
    """Block until the database accepts connections (Compose startup race)."""
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("database is ready after %d attempt(s)", attempt)
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            log.info("waiting for database (attempt %d) ...", attempt)
            time.sleep(1.0)


def init_db() -> None:
    """Create tables and seed the singleton revision row (revision 0)."""
    # Imported here so models register on Base.metadata before create_all().
    from . import models  # noqa: F401
    from .models import Base, RevisionMeta

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session, session.begin():
        if session.get(RevisionMeta, 1) is None:
            session.add(RevisionMeta(id=1, revision=0))

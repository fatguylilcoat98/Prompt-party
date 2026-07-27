"""Database engine and session management.

SQLite by default (zero-config, file under ./data — a directory Team Talk
never touches). The schema uses portable SQLAlchemy types so a later move
to PostgreSQL is a DATABASE_URL change.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import Base


def build_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        db_path = database_url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base


def make_engine(url: str | None = None) -> Engine:
    database_url = url or get_settings().database_url
    engine = create_engine(database_url)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


engine = make_engine()
SessionLocal = make_session_factory(engine)


def create_schema(target: Engine = engine) -> None:
    Base.metadata.create_all(target)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


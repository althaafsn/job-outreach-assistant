from __future__ import annotations

import importlib
from pathlib import Path

from sqlalchemy import inspect, text


def _load(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


def test_sqlite_is_configured_for_integrity_and_local_concurrency(tmp_path: Path) -> None:
    db_module = _load("app.db")
    assert db_module is not None
    engine = db_module.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() >= 5000


def test_schema_contains_pipeline_and_review_tables(tmp_path: Path) -> None:
    db_module = _load("app.db")
    assert db_module is not None
    engine = db_module.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db_module.create_schema(engine)
    assert {
        "ingest_messages",
        "pipeline_runs",
        "jobs",
        "job_sources",
        "contacts",
        "job_contacts",
        "contact_evidence",
        "contact_emails",
        "research_angles",
        "angle_evidence",
        "drafts",
        "outreach_events",
        "usage_counters",
    } <= set(inspect(engine).get_table_names())


def test_daily_quota_is_transactional_and_resets_by_date(tmp_path: Path) -> None:
    db_module = _load("app.db")
    quota_module = _load("app.quotas")
    assert db_module is not None
    assert quota_module is not None
    engine = db_module.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db_module.create_schema(engine)
    session_factory = db_module.make_session_factory(engine)
    with session_factory() as session:
        assert quota_module.reserve(session, "brave_search", 2, day="2026-07-18")
        assert quota_module.reserve(session, "brave_search", 2, day="2026-07-18")
        assert not quota_module.reserve(session, "brave_search", 2, day="2026-07-18")
        assert quota_module.used(session, "brave_search", day="2026-07-18") == 2
        assert quota_module.reserve(session, "brave_search", 2, day="2026-07-19")


def test_local_sqlite_parent_directory_is_created(tmp_path: Path) -> None:
    db_module = _load("app.db")
    assert db_module is not None
    database = tmp_path / "nested" / "local.db"
    engine = db_module.make_engine(f"sqlite:///{database}")
    db_module.create_schema(engine)
    assert database.exists()

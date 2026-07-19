from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.db import create_schema, make_engine, make_session_factory
from app.ingest import JobInput, upsert_job
from app.models import Job


def _module():
    try:
        from app import cli

        return cli
    except ImportError:
        return None


def test_parser_exposes_documented_local_commands() -> None:
    cli = _module()
    assert cli is not None
    parser = cli.build_parser()
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    assert {
        "init-db",
        "gmail-auth",
        "ingest",
        "import-text",
        "backfill",
        "research-pending",
        "extract-pending",
        "run-daily",
        "eval-ai",
        "export",
        "doctor",
        "serve",
    } <= set(action.choices)


def test_doctor_reports_missing_optional_integrations_without_exposing_values() -> None:
    cli = _module()
    assert cli is not None

    class Settings:
        database_url = "sqlite:///:memory:"
        openrouter_api_key = ""
        brave_api_key = ""
        gmail_credentials_file = __import__("pathlib").Path("/missing/client.json")
        gmail_token_file = __import__("pathlib").Path("/missing/token.json")

    result = cli.doctor_report(Settings())
    assert result["database"] == "ok"
    assert result["openrouter"] == "not configured"
    assert result["brave_search"] == "not configured"
    assert result["gmail"] == "not authorized"
    assert "sqlite" not in str(result).casefold()


def test_default_openrouter_limit_matches_free_account_allowance(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.delenv("OPENROUTER_DAILY_REQUEST_LIMIT", raising=False)
    assert Settings(_env_file=None).openrouter_daily_request_limit == 50


def test_contact_research_only_uses_verified_jobs(tmp_path: Path, monkeypatch) -> None:
    cli = _module()
    assert cli is not None
    engine = make_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    create_schema(engine)
    seen: list[int] = []

    def research(_session, job, _search, _ai, **_kwargs):
        seen.append(job.id)
        return 1

    monkeypatch.setattr(cli, "research_job", research)
    settings = SimpleNamespace(
        brave_api_key="test",
        openrouter_api_key="test",
        openrouter_model="openrouter/free",
        openrouter_daily_request_limit=50,
        research_department="",
    )
    with make_session_factory(engine)() as session:
        pending = upsert_job(
            session,
            JobInput(
                title="Pending",
                company="Needs review",
                description="Pending description",
                source="manual",
                external_id="pending",
            ),
        )
        verified = upsert_job(
            session,
            JobInput(
                title="Verified",
                company="Example",
                description="Verified description",
                source="manual",
                external_id="verified",
            ),
        )
        verified.quality_status = "verified"
        session.commit()

        assert cli._research_pending(settings, session) == 1
        assert seen == [verified.id]
        assert session.scalar(select(Job).where(Job.id == pending.id)) is not None

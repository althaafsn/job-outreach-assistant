from __future__ import annotations

import argparse


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
        google_api_key = ""
        google_search_engine_id = ""
        gmail_credentials_file = __import__("pathlib").Path("/missing/client.json")
        gmail_token_file = __import__("pathlib").Path("/missing/token.json")

    result = cli.doctor_report(Settings())
    assert result["database"] == "ok"
    assert result["openrouter"] == "not configured"
    assert result["google_search"] == "not configured"
    assert result["gmail"] == "not authorized"
    assert "sqlite" not in str(result).casefold()

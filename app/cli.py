from __future__ import annotations

import argparse
import json
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.ai import DeferredAI, OpenRouterClient, evaluate_contracts
from app.config import Settings, get_settings
from app.db import create_schema, make_engine, make_session_factory
from app.ingest import parse_job_text, upsert_job
from app.integrations import (
    BraveSearchClient,
    DeferredIntegration,
    gmail_service,
)
from app.models import Contact, ContactEvidence, Draft, Job, JobContact, ResearchAngle
from app.pipeline import (
    Step,
    backfill_jobs,
    generate_angles,
    ingest_gmail,
    research_job,
    run_steps,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-outreach")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    commands.add_parser("gmail-auth")
    commands.add_parser("ingest")

    import_text = commands.add_parser("import-text")
    import_text.add_argument("file", type=Path)
    import_text.add_argument("--company", default="")
    import_text.add_argument("--url")

    backfill = commands.add_parser("backfill")
    backfill.add_argument("--months", type=int, default=6)
    backfill.add_argument("--query")

    commands.add_parser("research-pending")
    commands.add_parser("run-daily")
    commands.add_parser("eval-ai")

    export = commands.add_parser("export")
    export.add_argument("output", type=Path)

    commands.add_parser("doctor")
    commands.add_parser("serve")
    return parser


def doctor_report(settings: Any) -> dict[str, str]:
    database = "error"
    try:
        with make_engine(settings.database_url).connect() as connection:
            connection.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        pass
    return {
        "database": database,
        "openrouter": "configured" if settings.openrouter_api_key else "not configured",
        "brave_search": "configured" if settings.brave_api_key else "not configured",
        "gmail": "authorized" if settings.gmail_token_file.exists() else "not authorized",
    }


def _runtime(settings: Settings) -> sessionmaker[Session]:
    engine = make_engine(settings.database_url)
    create_schema(engine)
    return make_session_factory(engine)


def _search(settings: Settings) -> BraveSearchClient:
    return BraveSearchClient(
        api_key=settings.brave_api_key,
    )


def _profile(settings: Settings) -> str:
    if settings.user_profile_file.exists():
        return settings.user_profile_file.read_text(encoding="utf-8")[:4000]
    return "Recent Computer Engineering graduate seeking entry-level data and software roles."


def _research_pending(settings: Settings, session: Session) -> int:
    search = _search(settings)
    count = 0
    jobs = session.scalars(select(Job).where(Job.duplicate_of_id.is_(None))).all()
    for job in jobs:
        has_contact = session.scalar(
            select(JobContact.id).where(JobContact.job_id == job.id).limit(1)
        )
        if not has_contact:
            count += research_job(
                session,
                job,
                search,
                department=settings.research_department,
            )
    return count


def _generate_pending(settings: Settings, session: Session) -> int:
    if not settings.openrouter_api_key:
        return 0
    client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        session=session,
        model=settings.openrouter_model,
        daily_limit=settings.openrouter_daily_request_limit,
    )
    count = 0
    for job in session.scalars(select(Job)):
        existing = session.scalar(
            select(ResearchAngle.id).where(ResearchAngle.job_id == job.id).limit(1)
        )
        if not existing:
            count += generate_angles(session, job, client, profile_summary=_profile(settings))
    return count


def _backfill_target(settings: Settings, session: Session, *, query: str) -> int:
    return backfill_jobs(
        session,
        _search(settings),
        query=f"{query} {settings.target_location}",
    )


def _export(session: Session) -> dict[str, Any]:
    return {
        "jobs": [
            {
                "id": row.id,
                "title": row.title,
                "company": row.company,
                "location": row.location,
                "description": row.description,
                "requisition_id": row.requisition_id,
                "url": row.canonical_url,
                "status": row.status,
                "notes": row.notes,
            }
            for row in session.scalars(select(Job))
        ],
        "contacts": [
            {
                "id": row.id,
                "name": row.name,
                "title": row.title,
                "company": row.company,
                "profile_url": row.profile_url,
                "evidence": [
                    {
                        "title": item.title,
                        "source_url": item.source_url,
                        "excerpt": item.excerpt,
                    }
                    for item in session.scalars(
                        select(ContactEvidence).where(ContactEvidence.contact_id == row.id)
                    )
                ],
            }
            for row in session.scalars(select(Contact))
        ],
        "angles": [
            {
                "id": row.id,
                "job_id": row.job_id,
                "contact_id": row.contact_id,
                "angle": row.angle,
                "question": row.question,
            }
            for row in session.scalars(select(ResearchAngle))
        ],
        "drafts": [
            {
                "id": row.id,
                "job_id": row.job_id,
                "contact_id": row.contact_id,
                "kind": row.kind,
                "subjects": json.loads(row.subject_options_json),
                "body": row.body,
            }
            for row in session.scalars(select(Draft))
        ],
    }


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("app.api:app", host=settings.app_host, port=settings.app_port)
        return
    if args.command == "doctor":
        print(json.dumps(doctor_report(settings), indent=2))
        return
    sessions = _runtime(settings)
    if args.command == "init-db":
        print("Database initialized.")
        return
    if args.command == "gmail-auth":
        gmail_service(
            settings.gmail_credentials_file,
            settings.gmail_token_file,
            interactive=True,
        )
        print("Gmail read-only authorization saved.")
        return
    with sessions() as session:
        if args.command == "import-text":
            incoming = parse_job_text(
                args.file.read_text(encoding="utf-8"),
                company=args.company,
                url=args.url,
            )
            row = upsert_job(session, incoming)
            print(json.dumps({"id": row.id, "title": row.title}))
        elif args.command == "ingest":
            service = gmail_service(
                settings.gmail_credentials_file,
                settings.gmail_token_file,
                interactive=False,
            )
            print(ingest_gmail(session, service, query=settings.gmail_query))
        elif args.command == "backfill":
            query = args.query or settings.target_job_queries.split("|")[0]
            print(
                backfill_jobs(
                    session,
                    _search(settings),
                    query=f"{query} {settings.target_location}",
                    months=args.months,
                )
            )
        elif args.command == "research-pending":
            print(_research_pending(settings, session))
        elif args.command == "eval-ai":
            fixtures = Path("evals/fixtures/contracts.json")
            rows = json.loads(fixtures.read_text(encoding="utf-8")) if fixtures.exists() else []
            result = evaluate_contracts(rows)
            result["status"] = "ok" if not result["failed"] else "failed"
            print(json.dumps(result))
        elif args.command == "export":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(_export(session), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(args.output)
        elif args.command == "run-daily":
            steps: list[Step] = []
            if settings.gmail_token_file.exists():
                service = gmail_service(
                    settings.gmail_credentials_file,
                    settings.gmail_token_file,
                    interactive=False,
                )
                steps.append(
                    (
                        "gmail",
                        lambda db: ingest_gmail(db, service, query=settings.gmail_query),
                    )
                )
            if settings.brave_api_key:
                for index, query in enumerate(settings.target_job_queries.split("|")):
                    steps.append(
                        (
                            f"backfill_{index}",
                            partial(_backfill_target, settings, query=query),
                        )
                    )
                steps.append(("contacts", lambda db: _research_pending(settings, db)))
            if settings.openrouter_api_key:
                steps.append(("angles", lambda db: _generate_pending(settings, db)))
            try:
                print(json.dumps(run_steps(sessions, Path("data/daily.lock"), steps), indent=2))
            except (DeferredAI, DeferredIntegration) as exc:
                print(json.dumps({"status": "deferred", "reason": str(exc)}))


if __name__ == "__main__":
    main()

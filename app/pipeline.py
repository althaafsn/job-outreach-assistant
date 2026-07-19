from __future__ import annotations

import fcntl
import json
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import DeferredAI, OpenRouterClient, build_angle_prompt
from app.ingest import JobInput, parse_gmail_raw, record_ingest_message, upsert_job
from app.integrations import (
    BraveSearchClient,
    DeferredIntegration,
    SearchResult,
    iter_gmail_raw,
    read_public_page,
)
from app.models import (
    AngleEvidence,
    Contact,
    ContactEvidence,
    Job,
    JobContact,
    PipelineRun,
    ResearchAngle,
)
from app.research import (
    ContactCandidate,
    contact_queries,
    rank_contacts,
    save_evidence,
    save_public_emails,
    save_recommendations,
)
from app.security import FetchRejected, UnsafeURL


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def pipeline_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunning("The daily pipeline is already running") from exc
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


Step = tuple[str, Callable[[Session], int]]


def run_steps(
    sessions: sessionmaker[Session], lock_path: Path, steps: Sequence[Step]
) -> dict[str, int]:
    with pipeline_lock(lock_path), sessions() as session:
        run = PipelineRun(kind="daily", status="running")
        session.add(run)
        session.commit()
        run_id = run.id
        counters: dict[str, int] = {}
        try:
            for name, step in steps:
                counters[name] = step(session)
            completed_run = session.get(PipelineRun, run_id)
            assert completed_run is not None
            completed_run.status = "completed"
            completed_run.finished_at = datetime.now(UTC)
            completed_run.counters_json = json.dumps(counters)
            session.commit()
            return counters
        except (DeferredAI, DeferredIntegration) as exc:
            session.rollback()
            deferred_run = session.get(PipelineRun, run_id)
            assert deferred_run is not None
            deferred_run.status = "deferred"
            deferred_run.finished_at = datetime.now(UTC)
            deferred_run.counters_json = json.dumps(counters)
            deferred_run.error = f"{type(exc).__name__}: {exc}"[:2000]
            session.commit()
            raise
        except Exception as exc:
            session.rollback()
            failed_run = session.get(PipelineRun, run_id)
            assert failed_run is not None
            failed_run.status = "failed"
            failed_run.finished_at = datetime.now(UTC)
            failed_run.counters_json = json.dumps(counters)
            failed_run.error = f"{type(exc).__name__}: {exc}"[:2000]
            session.commit()
            raise


def _title_before_link(text: str, link: str) -> str:
    job_id = link.rstrip("/").split("/")[-1]
    for line in text.splitlines():
        if job_id in line:
            title = re.sub(r"https?://\S+", "", line).strip(" -|")
            if title:
                return title[:300]
    return f"LinkedIn job {job_id}"


def ingest_gmail(session: Session, service: Any, *, query: str) -> int:
    imported = 0
    for raw_message in iter_gmail_raw(service, query=query):
        alert = parse_gmail_raw(raw_message["id"], raw_message["raw"])
        if not alert.links:
            continue
        if not record_ingest_message(
            session,
            "gmail",
            alert.external_id,
            alert.subject,
            alert.received_at,
        ):
            continue
        for link in alert.links:
            upsert_job(
                session,
                JobInput(
                    title=_title_before_link(alert.text, link),
                    company="Needs review",
                    description=(
                        "Imported from a Gmail job alert. Paste the public job "
                        "description to complete this record."
                    ),
                    url=link,
                    source="gmail",
                    external_id=f"{alert.external_id}:{link.rstrip('/').split('/')[-1]}",
                ),
            )
        imported += 1
    return imported


def backfill_jobs(
    session: Session,
    search: BraveSearchClient,
    *,
    query: str,
    months: int = 6,
) -> int:
    since = (datetime.now(UTC) - timedelta(days=max(1, min(months, 12)) * 30)).date()
    results = search.search(f"{query[:120]} jobs after:{since.isoformat()}")
    imported = 0
    for result in results:
        title, _, company = result.title.partition(" - ")
        upsert_job(
            session,
            JobInput(
                title=title[:300] or "Job search result",
                company=(company.split("|")[0].strip() or "Needs review")[:300],
                description=result.snippet,
                url=result.url,
                source="brave",
                external_id=result.url,
            ),
        )
        imported += 1
    return imported


def _candidate(result: SearchResult, company: str) -> ContactCandidate | None:
    title = re.sub(r"\s*[|–-]\s*LinkedIn.*$", "", result.title, flags=re.IGNORECASE)
    pieces = [piece.strip() for piece in re.split(r"\s+[|–-]\s+", title) if piece.strip()]
    if not pieces:
        return None
    name = pieces[0]
    role = pieces[1] if len(pieces) > 1 else result.snippet[:300]
    if len(name.split()) < 2 or len(name) > 100:
        return None
    return ContactCandidate(name=name, title=role, company=company, profile_url=result.url)


def research_job(
    session: Session,
    job: Job,
    search: BraveSearchClient,
    *,
    department: str = "",
    read_page: Callable[[str], str] = read_public_page,
) -> int:
    candidates: list[ContactCandidate] = []
    sources: dict[str, SearchResult] = {}
    for query in contact_queries(company=job.company, department=department, job_title=job.title):
        for result in search.search(query):
            candidate = _candidate(result, job.company)
            if candidate and candidate.profile_url not in sources:
                candidates.append(candidate)
                sources[candidate.profile_url or ""] = result
    ranked = rank_contacts(
        candidates,
        company=job.company,
        job_title=job.title,
        department=department,
    )
    ids = save_recommendations(session, job, ranked)
    for contact_id, candidate in zip(ids, ranked, strict=True):
        source_result = sources.get(candidate.profile_url or "")
        contact = session.get(Contact, contact_id)
        if source_result and source_result.snippet and contact:
            page_text = source_result.snippet
            try:
                page_text = read_page(source_result.url) or page_text
            except (FetchRejected, UnsafeURL, httpx.HTTPError):
                pass
            save_evidence(
                session,
                contact,
                title=source_result.title,
                source_url=source_result.url,
                excerpt=page_text,
                kind="public_third_party",
            )
            save_public_emails(
                session,
                contact,
                text=f"{source_result.title}\n{page_text}",
                source_url=source_result.url,
            )
    return len(ids)


def generate_angles(
    session: Session,
    job: Job,
    ai: OpenRouterClient,
    *,
    profile_summary: str,
) -> int:
    created = 0
    links = session.scalars(select(JobContact).where(JobContact.job_id == job.id)).all()
    for link in links:
        contact = session.get(Contact, link.contact_id)
        if not contact:
            continue
        evidence = session.scalars(
            select(ContactEvidence).where(ContactEvidence.contact_id == contact.id)
        ).all()
        if not evidence:
            continue
        prompt = build_angle_prompt(
            job={"title": job.title, "company": job.company},
            contact={"name": contact.name, "title": contact.title},
            evidence=[
                {
                    "id": row.id,
                    "title": row.title,
                    "excerpt": row.excerpt,
                    "source_url": row.source_url,
                }
                for row in evidence
            ],
            profile_summary=profile_summary,
        )
        result = ai.generate_angles(prompt, allowed_evidence_ids={row.id for row in evidence})
        for suggestion in result.value.angles:
            angle = ResearchAngle(
                job_id=job.id,
                contact_id=contact.id,
                angle=suggestion.angle,
                question=suggestion.question,
                model=result.model,
            )
            session.add(angle)
            session.flush()
            session.add_all(
                [
                    AngleEvidence(angle_id=angle.id, evidence_id=evidence_id)
                    for evidence_id in suggestion.evidence_ids
                ]
            )
            created += 1
        session.commit()
    return created

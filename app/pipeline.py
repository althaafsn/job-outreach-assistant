from __future__ import annotations

import fcntl
import hashlib
import json
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import (
    PROMPT_VERSION_JOBS,
    DeferredAI,
    OpenRouterClient,
    UngroundedOutput,
    build_angle_prompt,
    build_job_extraction_prompt,
    validate_job_extraction,
)
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
    JobSource,
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
    job_id = urlsplit(link).path.rstrip("/").split("/")[-1]
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


def _extraction_source(
    session: Session,
    job: Job,
    read_page: Callable[[str], str],
) -> str:
    manual = session.scalar(
        select(JobSource.id).where(
            JobSource.job_id == job.id,
            JobSource.source == "manual",
        )
    )
    if manual:
        return job.description
    if not job.canonical_url:
        raise FetchRejected("Job has no public source URL")
    return read_page(job.canonical_url)


def _posted_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _record_extraction_failure(
    session: Session,
    job: Job,
    *,
    quality_status: str,
    error: str,
    model: str | None = None,
    source_hash: str | None = None,
) -> Job:
    job.quality_status = quality_status
    job.extraction_error = error[:2000]
    job.extraction_model = model
    job.extraction_prompt_version = PROMPT_VERSION_JOBS
    job.extraction_attempts += 1
    job.extracted_at = datetime.now(UTC)
    job.source_content_hash = source_hash
    session.commit()
    return job


def extract_job(
    session: Session,
    job: Job,
    ai: Any,
    *,
    read_page: Callable[[str], str] = read_public_page,
) -> Job:
    try:
        source_text = _extraction_source(session, job, read_page)
    except (FetchRejected, UnsafeURL, httpx.HTTPError) as exc:
        return _record_extraction_failure(
            session,
            job,
            quality_status="needs_review",
            error=f"{type(exc).__name__}: {exc}",
        )
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    result = ai.extract_job(build_job_extraction_prompt(source_text))
    output = result.value
    if output.page_type != "individual_job":
        return _record_extraction_failure(
            session,
            job,
            quality_status="rejected",
            error=output.reason or f"Page classified as {output.page_type}",
            model=result.model,
            source_hash=source_hash,
        )
    try:
        description = validate_job_extraction(output, source_text)
    except UngroundedOutput as exc:
        return _record_extraction_failure(
            session,
            job,
            quality_status="needs_review",
            error=str(exc),
            model=result.model,
            source_hash=source_hash,
        )
    job.title = output.title.strip()
    job.company = output.company.strip()
    job.location = output.location.strip()
    job.requisition_id = output.requisition_id
    job.description = description
    job.posted_at = _posted_at(output.posted_at)
    job.quality_status = "verified"
    job.extraction_error = None
    job.extraction_model = result.model
    job.extraction_prompt_version = PROMPT_VERSION_JOBS
    job.extraction_attempts += 1
    job.extracted_at = datetime.now(UTC)
    job.source_content_hash = source_hash
    session.commit()
    return job


def extract_pending_jobs(
    session: Session,
    ai: Any,
    *,
    max_jobs: int = 50,
    read_page: Callable[[str], str] = read_public_page,
) -> int:
    rows = list(
        session.execute(
            select(Job, func.max(JobSource.discovered_at))
            .outerjoin(JobSource, JobSource.job_id == Job.id)
            .where(
                Job.quality_status == "pending",
                Job.duplicate_of_id.is_(None),
            )
            .group_by(Job.id)
        ).all()
    )
    rows.sort(
        key=lambda row: row[1] or row[0].created_at,
        reverse=True,
    )
    processed = 0
    for job, _discovered_at in rows[: max(0, max_jobs)]:
        try:
            extract_job(session, job, ai, read_page=read_page)
        except DeferredAI:
            break
        processed += 1
    return processed


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

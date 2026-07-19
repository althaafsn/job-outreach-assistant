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
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ai import (
    PROMPT_VERSION_JOBS,
    DeferredAI,
    OpenRouterClient,
    UngroundedOutput,
    build_angle_prompt,
    build_contact_selection_prompt,
    build_job_extraction_prompt,
    validate_job_extraction,
)
from app.domain import normalize_text
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
    contact_choice_rejection_reason,
    contact_focus_terms,
    contact_queries,
    evidence_rejection_reason,
    grounded_contact_title,
    person_research_queries,
    relevant_excerpt,
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
    results = search.search(
        f'site:linkedin.com/jobs/view "{query[:120]}" after:{since.isoformat()}'
    )
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


def research_job(
    session: Session,
    job: Job,
    search: BraveSearchClient,
    ai: OpenRouterClient,
    *,
    department: str = "",
    read_page: Callable[[str], str] = read_public_page,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    def report(**event: Any) -> None:
        if progress:
            progress(event)

    discovered: list[SearchResult] = []
    seen_urls: dict[str, int] = {}
    for query in contact_queries(
        company=job.company,
        department=department,
        job_title=job.title,
        description=job.description,
    ):
        results = search.search(query)
        report(event="search", phase="contact_discovery", query=query, results=len(results))
        for result in results:
            existing_index = seen_urls.get(result.url)
            if existing_index is None:
                seen_urls[result.url] = len(discovered)
                discovered.append(result)
            else:
                existing = discovered[existing_index]
                if len(f"{result.title} {result.snippet}") > len(
                    f"{existing.title} {existing.snippet}"
                ):
                    discovered[existing_index] = result
    numbered = {index: result for index, result in enumerate(discovered, start=1)}
    if not numbered:
        return 0
    focus_terms = contact_focus_terms(job.description, department)
    selection_texts: dict[int, str] = {}
    page_cache: dict[str, str] = {}
    candidate_page_budget = 8
    for result_id, result in numbered.items():
        text = f"{result.title}\n{result.snippet}"
        host = (urlsplit(result.url).hostname or "").casefold()
        is_linkedin = host == "linkedin.com" or host.endswith(".linkedin.com")
        if candidate_page_budget and not is_linkedin:
            candidate_page_budget -= 1
            try:
                page_cache[result.url] = read_page(result.url)
                text = (
                    f"{text}\n"
                    f"{relevant_excerpt(page_cache[result.url], focus_terms)}"
                )
                report(
                    event="candidate_page",
                    decision="accepted",
                    url=result.url,
                )
            except (FetchRejected, UnsafeURL, httpx.HTTPError) as exc:
                report(
                    event="candidate_page",
                    decision="rejected",
                    url=result.url,
                    reason=str(exc),
                )
        selection_texts[result_id] = text
    selection = ai.select_contacts(
        build_contact_selection_prompt(
            job={
                "title": job.title,
                "company": job.company,
                "department": department,
                "description": job.description,
            },
            results=[
                {
                    "id": result_id,
                    "title": result.title,
                    "url": result.url,
                    "snippet": selection_texts[result_id],
                }
                for result_id, result in numbered.items()
            ],
        ),
        allowed_result_ids=set(numbered),
    )
    report(
        event="model",
        task="contact_selection",
        model=selection.model,
        selected=len(selection.value.contacts),
    )
    candidates: list[ContactCandidate] = []
    for rank, choice in enumerate(selection.value.contacts, start=1):
        source = numbered[choice.result_id]
        source_text = normalize_text(selection_texts[choice.result_id])
        if normalize_text(choice.name) not in source_text:
            report(
                event="contact",
                decision="rejected",
                person=choice.name,
                reason="Selected name is not grounded in the search result",
            )
            continue
        grounded_title = grounded_contact_title(
            name=choice.name,
            proposed=choice.title,
            source_text=selection_texts[choice.result_id],
        )
        if grounded_title is None:
            report(
                event="contact",
                decision="rejected",
                person=choice.name,
                title=choice.title,
                reason="Selected title is not grounded in the search result",
            )
            continue
        relevance_reason = contact_choice_rejection_reason(
            name=choice.name,
            title=grounded_title,
            source_text=selection_texts[choice.result_id],
            focus_terms=focus_terms,
        )
        if relevance_reason:
            report(
                event="contact",
                decision="rejected",
                person=choice.name,
                reason=relevance_reason,
            )
            continue
        candidates.append(
            ContactCandidate(
                name=choice.name,
                title=grounded_title,
                company=job.company,
                profile_url=source.url,
                score=float(101 - rank),
                rationale=choice.rationale,
            )
        )
        report(
            event="candidate",
            decision="accepted",
            person=choice.name,
            title=grounded_title,
            url=source.url,
        )
    ids = save_recommendations(session, job, candidates)
    for contact_id, _candidate in zip(ids, candidates, strict=True):
        contact = session.get(Contact, contact_id)
        if not contact:
            continue
        evidence_count = 0
        researched_urls: set[str] = set()
        for query in person_research_queries(name=contact.name, company=job.company):
            results = search.search(query)
            report(
                event="search",
                phase="person_research",
                person=contact.name,
                query=query,
                results=len(results),
            )
            for result in results:
                if result.url in researched_urls or evidence_count >= 3:
                    continue
                researched_urls.add(result.url)
                host = (urlsplit(result.url).hostname or "").casefold()
                if host == "linkedin.com" or host.endswith(".linkedin.com"):
                    report(
                        event="source",
                        decision="rejected",
                        person=contact.name,
                        url=result.url,
                        reason="LinkedIn is a profile link, not a research source",
                    )
                    continue
                try:
                    page_text = page_cache.get(result.url) or read_page(result.url)
                except (FetchRejected, UnsafeURL, httpx.HTTPError) as exc:
                    report(
                        event="source",
                        decision="rejected",
                        person=contact.name,
                        url=result.url,
                        reason=str(exc),
                    )
                    continue
                combined = (
                    f"{result.title}\n{result.snippet}\n"
                    f"{relevant_excerpt(page_text, [contact.name, contact.company])}"
                )
                reason = evidence_rejection_reason(
                    combined,
                    person_name=contact.name,
                    source_url=result.url,
                    organization=contact.company,
                )
                if reason:
                    report(
                        event="source",
                        decision="rejected",
                        person=contact.name,
                        url=result.url,
                        reason=reason,
                    )
                    continue
                evidence = save_evidence(
                    session,
                    contact,
                    title=result.title,
                    source_url=result.url,
                    excerpt=combined,
                    kind="public_third_party",
                )
                if evidence is None:
                    continue
                evidence_count += 1
                report(
                    event="source",
                    decision="accepted",
                    person=contact.name,
                    url=result.url,
                    evidence_id=evidence.id,
                )
                save_public_emails(
                    session,
                    contact,
                    text=combined,
                    source_url=result.url,
                )
        has_evidence = session.scalar(
            select(ContactEvidence.id)
            .where(ContactEvidence.contact_id == contact.id)
            .limit(1)
        )
        if has_evidence is None:
            session.execute(
                delete(JobContact).where(
                    JobContact.job_id == job.id,
                    JobContact.contact_id == contact.id,
                    JobContact.status == "suggested",
                )
            )
            session.commit()
            report(
                event="contact",
                decision="rejected",
                person=contact.name,
                reason="No public source confirmed this person and organization",
            )
        else:
            report(
                event="contact",
                decision="accepted",
                person=contact.name,
                title=contact.title,
                url=contact.profile_url,
            )
    return len(
        session.scalars(
            select(JobContact).where(
                JobContact.job_id == job.id,
                JobContact.contact_id.in_(ids),
            )
        ).all()
    )


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

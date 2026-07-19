from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, desc, func, select
from sqlalchemy.orm import Session

from app.ai import (
    DeferredAI,
    DraftOutput,
    OpenRouterClient,
    build_draft_prompt,
)
from app.config import get_settings
from app.db import engine as default_engine
from app.db import make_session_factory
from app.ingest import parse_job_text, upsert_job
from app.integrations import BraveSearchClient, DeferredIntegration
from app.models import (
    AngleEvidence,
    Contact,
    ContactEmail,
    ContactEvidence,
    Draft,
    IngestMessage,
    Job,
    JobContact,
    JobSource,
    OutreachEvent,
    PipelineRun,
    ResearchAngle,
    UsageCounter,
)
from app.pipeline import extract_job, generate_angles, research_job
from app.research import ContactCandidate, save_evidence, save_recommendations


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobImport(RequestModel):
    text: str = Field(min_length=1)
    company: str = Field(default="", max_length=300)
    url: str | None = Field(default=None, max_length=1000)


class JobPatch(RequestModel):
    status: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=10_000)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    company: str | None = Field(default=None, min_length=1, max_length=300)
    location: str | None = Field(default=None, max_length=300)


class ContactCreate(RequestModel):
    name: str = Field(min_length=1, max_length=300)
    title: str = Field(default="", max_length=300)
    company: str = Field(min_length=1, max_length=300)
    profile_url: str | None = Field(default=None, max_length=1000)


class EvidenceCreate(RequestModel):
    title: str = Field(min_length=1, max_length=500)
    source_url: str = Field(min_length=1, max_length=1000)
    excerpt: str = Field(min_length=1, max_length=5000)
    kind: str = Field(default="professional", max_length=32)


class AngleCreate(RequestModel):
    angle: str = Field(min_length=10, max_length=500)
    question: str = Field(min_length=10, max_length=500)
    evidence_ids: list[int] = Field(min_length=1, max_length=4)


class DraftCreate(RequestModel):
    kind: str
    subjects: list[str] = Field(default_factory=list)
    body: str
    angle_id: int | None = None


class OutreachCreate(RequestModel):
    job_id: int | None = None
    contact_id: int | None = None
    draft_id: int | None = None
    type: str = Field(min_length=1, max_length=40)
    follow_up_at: datetime | None = None
    notes: str = Field(default="", max_length=5000)


class GenerateDraft(RequestModel):
    kind: str
    angle_id: int
    user_context: str = Field(default="", max_length=1500)


class AnglePerspective(RequestModel):
    perspective: str = Field(default="", max_length=1000)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


Json = dict[str, Any]


def _job(job: Job) -> Json:
    priority, reasons = _job_priority(job)
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": job.description,
        "requisition_id": job.requisition_id,
        "url": job.canonical_url,
        "status": job.status,
        "quality_status": job.quality_status,
        "extraction_error": job.extraction_error,
        "extraction_model": job.extraction_model,
        "extracted_at": _iso(job.extracted_at),
        "notes": job.notes,
        "suspected_duplicate": job.suspected_duplicate,
        "posted_at": _iso(job.posted_at),
        "created_at": _iso(job.created_at),
        "priority": priority,
        "priority_reasons": reasons,
    }


def _job_priority(job: Job) -> tuple[int, list[str]]:
    """Return a transparent, local-only recommendation score for queue ordering."""
    settings = get_settings()
    title = job.title.casefold()
    score = 0
    reasons: list[str] = []
    queries = [
        item.strip().casefold() for item in settings.target_job_queries.split("|") if item.strip()
    ]
    for query in queries:
        if query in title:
            score += 40
            reasons.append(f"Matches target role: {query}")
            break
    stop_words = {"junior", "senior", "entry", "level", "and", "the", "of", "for"}
    target_words = {
        word
        for query in queries
        for word in query.split()
        if len(word) > 2 and word not in stop_words
    }
    overlap = sorted(word for word in target_words if word in title)
    if overlap:
        score += min(len(overlap) * 10, 30)
        reasons.append(f"Role terms: {', '.join(overlap[:3])}")

    location = job.location.casefold()
    if "vancouver" in location:
        score += 25
        reasons.append("Vancouver")
    elif "toronto" in location:
        score += 20
        reasons.append("Toronto")
    elif location:
        score += 10
        reasons.append("Located in Canada or another specified region")

    if job.posted_at:
        age = datetime.now(UTC) - (
            job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=UTC)
        )
        if age <= timedelta(days=7):
            score += 20
            reasons.append("Posted within 7 days")
        elif age <= timedelta(days=30):
            score += 10
            reasons.append("Posted within 30 days")
        elif age > timedelta(days=90):
            score -= 10
    collection_terms = ("jobs", "job openings", "discover", "overview", "opportunities")
    source_text = f"{job.canonical_url or ''} {job.description}".casefold()
    collection_signal = any(term in title for term in collection_terms) or any(
        term in source_text
        for term in ("jobsearch", "marketreport", "search?", "job postings", "view 37 job")
    )
    if not job.requisition_id and collection_signal:
        score -= 50
        reasons.append("Looks like a collection page")
    if not job.company:
        score -= 15
    if not job.location:
        score -= 10
    return score, reasons[:4]


def _job_sort_key(job: Job, sort: str) -> tuple[Any, ...]:
    if sort == "company":
        return (job.company.casefold(), job.title.casefold(), job.id)
    if sort == "newest":
        value = job.posted_at or job.created_at
        return (-(value.timestamp() if value else 0), job.id)
    score, _ = _job_priority(job)
    value = job.posted_at or job.created_at
    return (-score, -(value.timestamp() if value else 0), job.id)


def _location_matches(job: Job, group: str | None) -> bool:
    if not group:
        return True
    location = job.location.casefold()
    if group == "vancouver":
        return "vancouver" in location
    if group == "toronto":
        return "toronto" in location
    if group == "unknown":
        return not location
    if group == "canada":
        return bool(location)
    if group == "elsewhere_canada":
        return bool(location) and "vancouver" not in location and "toronto" not in location
    return True


def _outreach_items(session: Session) -> list[Json]:
    sent_types = {"connection_sent", "message_sent", "email_sent"}
    now = datetime.now(UTC)
    items: list[Json] = []
    drafts = session.scalars(select(Draft).order_by(desc(Draft.created_at))).all()
    for draft in drafts:
        job = session.get(Job, draft.job_id)
        contact = session.get(Contact, draft.contact_id)
        if not job or not contact:
            continue
        events = session.scalars(
            select(OutreachEvent)
            .where(OutreachEvent.draft_id == draft.id)
            .order_by(desc(OutreachEvent.occurred_at))
        ).all()
        latest_sent = next((event for event in events if event.type in sent_types), None)
        latest_follow_up = next((event for event in events if event.follow_up_at), None)
        follow_up_at = latest_follow_up.follow_up_at if latest_follow_up else None
        if follow_up_at:
            follow_up_value = (
                follow_up_at if follow_up_at.tzinfo else follow_up_at.replace(tzinfo=UTC)
            )
            state = "follow_up_due" if follow_up_value <= now else "follow_up_scheduled"
        elif latest_sent:
            state = "sent"
        else:
            state = "draft"
        items.append(
            {
                "id": draft.id,
                "state": state,
                "channel": draft.kind,
                "created_at": _iso(draft.created_at),
                "sent_at": _iso(latest_sent.occurred_at if latest_sent else None),
                "follow_up_at": _iso(follow_up_at),
                "draft": {
                    "id": draft.id,
                    "kind": draft.kind,
                    "subjects": json.loads(draft.subject_options_json),
                    "body": draft.body,
                },
                "job": _job(job),
                "contact": _contact(contact),
            }
        )
    return items


def _contact(contact: Contact) -> Json:
    return {
        "id": contact.id,
        "name": contact.name,
        "title": contact.title,
        "company": contact.company,
        "profile_url": contact.profile_url,
        "notes": contact.notes,
    }


def create_app(target_engine: Engine = default_engine) -> FastAPI:
    app = FastAPI(title="Job Outreach Assistant", version="0.1.0")
    sessions = make_session_factory(target_engine)

    def db() -> Iterator[Session]:
        with sessions() as session:
            yield session

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> Json:
        return {"status": "ok"}

    @app.get("/api/settings")
    def settings_status() -> Json:
        settings = get_settings()
        return {
            "openrouter_configured": bool(settings.openrouter_api_key),
            "brave_search_configured": bool(settings.brave_api_key),
            "gmail_authorized": settings.gmail_token_file.exists(),
            "openrouter_model": settings.openrouter_model,
            "openrouter_daily_limit": settings.openrouter_daily_request_limit,
            "target_job_queries": settings.target_job_queries.split("|"),
            "target_location": settings.target_location,
        }

    @app.get("/api/dashboard")
    def dashboard(session: Session = Depends(db)) -> Json:
        statuses: dict[str, int] = {
            job_status: count
            for job_status, count in session.execute(
                select(Job.status, func.count()).group_by(Job.status)
            )
        }
        all_jobs = session.scalars(
            select(Job).where(
                Job.duplicate_of_id.is_(None),
                Job.quality_status == "verified",
            )
        ).all()
        quality_counts = {
            quality: count
            for quality, count in session.execute(
                select(Job.quality_status, func.count()).group_by(Job.quality_status)
            )
        }
        new_jobs = sorted(
            (row for row in all_jobs if row.status == "new"),
            key=lambda row: _job_sort_key(row, "recommended"),
        )
        interested_jobs = sorted(
            (row for row in all_jobs if row.status == "interested"),
            key=lambda row: _job_sort_key(row, "recommended"),
        )
        latest_run = session.scalar(select(PipelineRun).order_by(desc(PipelineRun.id)).limit(1))
        outreach_items = _outreach_items(session)
        followups = [item for item in outreach_items if item["state"] == "follow_up_due"]
        drafts = [item for item in outreach_items if item["state"] == "draft"]
        if followups:
            next_action = {"type": "follow_up", **followups[0]}
        elif drafts:
            next_action = {"type": "review_draft", **drafts[0]}
        elif interested_jobs:
            next_action = {"type": "continue_job", "job": _job(interested_jobs[0])}
        elif new_jobs:
            next_action = {"type": "review_job", "job": _job(new_jobs[0])}
        else:
            next_action = {"type": "import_job"}
        return {
            "jobs": {
                "total": sum(statuses.values()),
                "new": statuses.get("new", 0),
                "applied": statuses.get("applied", 0),
                "archived": statuses.get("archived", 0),
                "quality": quality_counts,
            },
            "contacts": session.scalar(select(func.count(Contact.id))) or 0,
            "follow_ups": session.scalar(
                select(func.count(OutreachEvent.id)).where(OutreachEvent.follow_up_at.is_not(None))
            )
            or 0,
            "usage": [
                {"day": row.day.isoformat(), "kind": row.kind, "used": row.used}
                for row in session.scalars(select(UsageCounter).order_by(UsageCounter.day.desc()))
            ],
            "runs": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "status": row.status,
                    "started_at": _iso(row.started_at),
                    "finished_at": _iso(row.finished_at),
                    "error": row.error,
                }
                for row in session.scalars(
                    select(PipelineRun).order_by(PipelineRun.id.desc()).limit(8)
                )
            ],
            "automation": {
                "last_run": {
                    "status": latest_run.status,
                    "kind": latest_run.kind,
                    "started_at": _iso(latest_run.started_at),
                    "finished_at": _iso(latest_run.finished_at),
                    "error": latest_run.error,
                }
                if latest_run
                else None,
                "next_run": "Weekdays at 08:05",
            },
            "next_action": next_action,
            "queues": {
                "new_jobs": [_job(row) for row in new_jobs[:10]],
                "interested_jobs": [_job(row) for row in interested_jobs[:10]],
                "drafts": drafts[:10],
                "follow_ups": followups[:10],
            },
        }

    @app.get("/api/jobs")
    def jobs(
        status_filter: str | None = None,
        q: str | None = None,
        location_group: str | None = None,
        posted_within: int | None = None,
        source: str | None = None,
        quality_filter: str = "verified",
        sort: Literal["recommended", "newest", "company"] = "recommended",
        offset: int = 0,
        limit: int = 50,
        session: Session = Depends(db),
    ) -> Json:
        query = select(Job).where(Job.duplicate_of_id.is_(None))
        if quality_filter != "all":
            qualities = [item.strip() for item in quality_filter.split(",") if item.strip()]
            query = query.where(Job.quality_status.in_(qualities or ["verified"]))
        if status_filter:
            query = query.where(
                Job.status.in_([item.strip() for item in status_filter.split(",") if item.strip()])
            )
        if q:
            pattern = f"%{q[:100]}%"
            query = query.where(
                Job.title.ilike(pattern)
                | Job.company.ilike(pattern)
                | Job.description.ilike(pattern)
            )
        if source:
            query = query.where(
                Job.id.in_(select(JobSource.job_id).where(JobSource.source == source))
            )
        rows = session.scalars(query).all()
        rows = [row for row in rows if _location_matches(row, location_group)]
        if posted_within is not None:
            cutoff = datetime.now(UTC) - timedelta(days=max(0, posted_within))
            rows = [
                row
                for row in rows
                if row.posted_at
                and (row.posted_at if row.posted_at.tzinfo else row.posted_at.replace(tzinfo=UTC))
                >= cutoff
            ]
        rows.sort(key=lambda row: _job_sort_key(row, sort))
        status_facets: dict[str, int] = {}
        location_facets: dict[str, int] = {}
        for row in rows:
            status_facets[row.status] = status_facets.get(row.status, 0) + 1
            location_key = (
                "vancouver"
                if "vancouver" in row.location.casefold()
                else "toronto"
                if "toronto" in row.location.casefold()
                else "unknown"
                if not row.location
                else "elsewhere_canada"
            )
            location_facets[location_key] = location_facets.get(location_key, 0) + 1
        source_facets: dict[str, int] = {}
        if rows:
            for source_name in session.scalars(
                select(JobSource.source)
                .where(JobSource.job_id.in_([row.id for row in rows]))
                .distinct()
            ):
                source_facets[source_name] = source_facets.get(source_name, 0) + 1
        safe_offset = max(offset, 0)
        safe_limit = min(max(limit, 1), 100)
        page = rows[safe_offset : safe_offset + safe_limit]
        return {
            "items": [_job(row) for row in page],
            "total": len(rows),
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": safe_offset + safe_limit < len(rows),
            "facets": {
                "status": status_facets,
                "location": location_facets,
                "source": source_facets,
            },
        }

    def require_job(job_id: int, session: Session) -> Job:
        row = session.get(Job, job_id)
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        return row

    def require_contact(contact_id: int, session: Session) -> Contact:
        row = session.get(Contact, contact_id)
        if not row:
            raise HTTPException(status_code=404, detail="Contact not found")
        return row

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: int, session: Session = Depends(db)) -> Json:
        row = require_job(job_id, session)
        result = _job(row)
        contacts: list[Json] = []
        links = session.scalars(
            select(JobContact).where(JobContact.job_id == job_id).order_by(JobContact.rank)
        ).all()
        for link in links:
            contact = require_contact(link.contact_id, session)
            item = _contact(contact)
            item.update(
                {
                    "rank": link.rank,
                    "score": link.score,
                    "rationale": link.rationale,
                    "evidence": [
                        {
                            "id": evidence.id,
                            "title": evidence.title,
                            "source_url": evidence.source_url,
                            "excerpt": evidence.excerpt,
                            "kind": evidence.kind,
                        }
                        for evidence in session.scalars(
                            select(ContactEvidence).where(ContactEvidence.contact_id == contact.id)
                        )
                    ],
                    "emails": [
                        {
                            "id": email.id,
                            "email": email.email,
                            "confidence": email.confidence,
                            "source_url": email.source_url,
                        }
                        for email in session.scalars(
                            select(ContactEmail).where(ContactEmail.contact_id == contact.id)
                        )
                    ],
                    "angles": [
                        {
                            "id": angle.id,
                            "angle": angle.angle,
                            "question": angle.question,
                            "status": angle.status,
                            "evidence_ids": session.scalars(
                                select(AngleEvidence.evidence_id).where(
                                    AngleEvidence.angle_id == angle.id
                                )
                            ).all(),
                        }
                        for angle in session.scalars(
                            select(ResearchAngle).where(
                                ResearchAngle.job_id == job_id,
                                ResearchAngle.contact_id == contact.id,
                            )
                        )
                    ],
                }
            )
            contacts.append(item)
        result["contacts"] = contacts
        result["drafts"] = [
            {
                "id": draft.id,
                "contact_id": draft.contact_id,
                "angle_id": draft.angle_id,
                "kind": draft.kind,
                "subjects": json.loads(draft.subject_options_json),
                "body": draft.body,
            }
            for draft in session.scalars(
                select(Draft).where(Draft.job_id == job_id).order_by(Draft.id.desc())
            )
        ]
        return result

    @app.post("/api/jobs/import", status_code=status.HTTP_201_CREATED)
    def import_job(body: JobImport, session: Session = Depends(db)) -> Json:
        incoming = parse_job_text(body.text, company=body.company, url=body.url)
        incoming.description = body.text
        incoming.external_id = f"manual-{uuid.uuid4()}"
        row = upsert_job(session, incoming)
        return _job(row)

    @app.post("/api/jobs/{job_id}/extract")
    def extract_one_job(job_id: int, session: Session = Depends(db)) -> Json:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise HTTPException(status_code=409, detail="OpenRouter is not configured")
        job = require_job(job_id, session)
        job.quality_status = "pending"
        job.extraction_error = None
        session.commit()
        try:
            extract_job(
                session,
                job,
                OpenRouterClient(
                    api_key=settings.openrouter_api_key,
                    session=session,
                    model=settings.openrouter_model,
                    daily_limit=settings.openrouter_daily_request_limit,
                ),
            )
        except DeferredAI as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _job(job)

    @app.post("/api/workflow/analyze")
    def analyze_workflow(body: JobImport, session: Session = Depends(db)) -> Json:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise HTTPException(
                status_code=409,
                detail="OpenRouter is not configured. Add OPENROUTER_API_KEY to .env.",
            )
        incoming = parse_job_text(body.text, company=body.company, url=body.url)
        incoming.description = body.text
        incoming.external_id = f"manual-{uuid.uuid4()}"
        job = upsert_job(session, incoming)
        ai = OpenRouterClient(
            api_key=settings.openrouter_api_key,
            session=session,
            model=settings.openrouter_model,
            daily_limit=settings.openrouter_daily_request_limit,
        )
        warnings: list[str] = []
        try:
            extract_job(session, job, ai)
        except DeferredAI as exc:
            warnings.append(str(exc))
            return {
                "stage": "pending",
                "warnings": warnings,
                "job": job_detail(job.id, session),
            }
        if job.quality_status != "verified":
            return {
                "stage": job.quality_status,
                "warnings": [job.extraction_error] if job.extraction_error else [],
                "job": job_detail(job.id, session),
            }
        if not settings.brave_api_key:
            return {
                "stage": "job_verified",
                "warnings": ["Brave Search is not configured."],
                "job": job_detail(job.id, session),
            }
        try:
            research_job(
                session,
                job,
                BraveSearchClient(api_key=settings.brave_api_key),
                department=settings.research_department,
            )
        except DeferredIntegration as exc:
            warnings.append(str(exc))
        if session.scalar(select(JobContact.id).where(JobContact.job_id == job.id)):
            profile = (
                settings.user_profile_file.read_text(encoding="utf-8")[:4000]
                if settings.user_profile_file.exists()
                else "Recent Computer Engineering graduate seeking entry-level roles."
            )
            try:
                generate_angles(session, job, ai, profile_summary=profile)
            except DeferredAI as exc:
                warnings.append(str(exc))
        detail = job_detail(job.id, session)
        has_angles = any(contact["angles"] for contact in detail["contacts"])
        return {
            "stage": "complete" if has_angles else "people_found",
            "warnings": warnings,
            "job": detail,
        }

    @app.patch("/api/jobs/{job_id}")
    def patch_job(job_id: int, body: JobPatch, session: Session = Depends(db)) -> Json:
        row = require_job(job_id, session)
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(row, field, value)
        session.commit()
        return _job(row)

    @app.post("/api/jobs/{job_id}/contacts", status_code=status.HTTP_201_CREATED)
    def add_contact(job_id: int, body: ContactCreate, session: Session = Depends(db)) -> Json:
        job = require_job(job_id, session)
        ids = save_recommendations(
            session,
            job,
            [ContactCandidate(body.name, body.title, body.company, body.profile_url)],
        )
        return _contact(require_contact(ids[0], session))

    @app.post("/api/jobs/{job_id}/research")
    def research_contacts(job_id: int, session: Session = Depends(db)) -> Json:
        settings = get_settings()
        if not settings.brave_api_key:
            raise HTTPException(
                status_code=409,
                detail="Brave Search is not configured. Add BRAVE_API_KEY to .env.",
            )
        try:
            count = research_job(
                session,
                require_job(job_id, session),
                BraveSearchClient(
                    api_key=settings.brave_api_key,
                ),
                department=settings.research_department,
            )
        except DeferredIntegration as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"contacts": count}

    @app.post("/api/jobs/{job_id}/angles/generate")
    def generate_job_angles(
        job_id: int,
        body: AnglePerspective,
        session: Session = Depends(db),
    ) -> Json:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise HTTPException(
                status_code=409,
                detail="OpenRouter is not configured. Add OPENROUTER_API_KEY to .env.",
            )
        profile = (
            settings.user_profile_file.read_text(encoding="utf-8")[:4000]
            if settings.user_profile_file.exists()
            else "Recent Computer Engineering graduate seeking entry-level data and software roles."
        )
        if body.perspective.strip():
            profile += f"\nUser-selected research perspective: {body.perspective.strip()}"
        try:
            count = generate_angles(
                session,
                require_job(job_id, session),
                OpenRouterClient(
                    api_key=settings.openrouter_api_key,
                    session=session,
                    model=settings.openrouter_model,
                    daily_limit=settings.openrouter_daily_request_limit,
                ),
                profile_summary=profile,
            )
        except DeferredAI as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"angles": count}

    @app.post(
        "/api/contacts/{contact_id}/evidence",
        status_code=status.HTTP_201_CREATED,
    )
    def add_evidence(contact_id: int, body: EvidenceCreate, session: Session = Depends(db)) -> Json:
        evidence = save_evidence(
            session,
            require_contact(contact_id, session),
            **body.model_dump(),
        )
        return {
            "id": evidence.id,
            "title": evidence.title,
            "source_url": evidence.source_url,
            "excerpt": evidence.excerpt,
            "kind": evidence.kind,
        }

    @app.post(
        "/api/jobs/{job_id}/contacts/{contact_id}/angles",
        status_code=status.HTTP_201_CREATED,
    )
    def add_angle(
        job_id: int,
        contact_id: int,
        body: AngleCreate,
        session: Session = Depends(db),
    ) -> Json:
        require_job(job_id, session)
        require_contact(contact_id, session)
        valid_ids = set(
            session.scalars(
                select(ContactEvidence.id).where(
                    ContactEvidence.contact_id == contact_id,
                    ContactEvidence.id.in_(body.evidence_ids),
                )
            )
        )
        if valid_ids != set(body.evidence_ids):
            raise HTTPException(status_code=422, detail="Evidence does not belong to contact")
        angle = ResearchAngle(
            job_id=job_id,
            contact_id=contact_id,
            angle=body.angle,
            question=body.question,
            status="selected",
            prompt_version="manual",
        )
        session.add(angle)
        session.flush()
        session.add_all(
            [
                AngleEvidence(angle_id=angle.id, evidence_id=evidence_id)
                for evidence_id in body.evidence_ids
            ]
        )
        session.commit()
        return {
            "id": angle.id,
            "angle": angle.angle,
            "question": angle.question,
            "evidence_ids": body.evidence_ids,
        }

    @app.post(
        "/api/jobs/{job_id}/contacts/{contact_id}/drafts",
        status_code=status.HTTP_201_CREATED,
    )
    def add_draft(
        job_id: int,
        contact_id: int,
        body: DraftCreate,
        session: Session = Depends(db),
    ) -> Json:
        require_job(job_id, session)
        require_contact(contact_id, session)
        validated = DraftOutput.model_validate(body.model_dump(exclude={"angle_id"}))
        draft = Draft(
            job_id=job_id,
            contact_id=contact_id,
            angle_id=body.angle_id,
            kind=validated.kind,
            subject_options_json=json.dumps(validated.subjects),
            body=validated.body,
            prompt_version="manual",
        )
        session.add(draft)
        session.commit()
        return {
            "id": draft.id,
            "kind": draft.kind,
            "subjects": validated.subjects,
            "body": draft.body,
        }

    @app.post(
        "/api/jobs/{job_id}/contacts/{contact_id}/drafts/generate",
        status_code=status.HTTP_201_CREATED,
    )
    def generate_draft(
        job_id: int,
        contact_id: int,
        body: GenerateDraft,
        session: Session = Depends(db),
    ) -> Json:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise HTTPException(status_code=409, detail="OpenRouter is not configured")
        job = require_job(job_id, session)
        contact = require_contact(contact_id, session)
        angle = session.scalar(
            select(ResearchAngle).where(
                ResearchAngle.id == body.angle_id,
                ResearchAngle.job_id == job_id,
                ResearchAngle.contact_id == contact_id,
            )
        )
        if not angle:
            raise HTTPException(status_code=404, detail="Angle not found")
        prompt = build_draft_prompt(
            kind=body.kind,
            user_context=body.user_context,
            job_title=job.title,
            company=job.company,
            contact_name=contact.name,
            angle=angle.angle,
            question=angle.question,
        )
        try:
            result = OpenRouterClient(
                api_key=settings.openrouter_api_key,
                session=session,
                model=settings.openrouter_model,
                daily_limit=settings.openrouter_daily_request_limit,
            ).generate_draft(prompt)
        except DeferredAI as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        draft = Draft(
            job_id=job_id,
            contact_id=contact_id,
            angle_id=angle.id,
            kind=result.value.kind,
            subject_options_json=json.dumps(result.value.subjects),
            body=result.value.body,
            model=result.model,
        )
        session.add(draft)
        session.commit()
        return {
            "id": draft.id,
            "kind": draft.kind,
            "subjects": result.value.subjects,
            "body": draft.body,
            "model": result.model,
        }

    @app.post("/api/outreach-events", status_code=status.HTTP_201_CREATED)
    def add_outreach(body: OutreachCreate, session: Session = Depends(db)) -> Json:
        if body.type not in {
            "connection_sent",
            "message_sent",
            "email_sent",
            "reply_received",
            "follow_up_sent",
        }:
            raise HTTPException(status_code=422, detail="Unsupported outreach event type")
        event = OutreachEvent(**body.model_dump())
        session.add(event)
        session.commit()
        return {"id": event.id, "type": event.type, "occurred_at": _iso(event.occurred_at)}

    @app.get("/api/outreach")
    def outreach(session: Session = Depends(db)) -> Json:
        return {"items": _outreach_items(session)}

    @app.get("/api/contacts")
    def contacts(session: Session = Depends(db)) -> Json:
        return {
            "items": [
                _contact(row) for row in session.scalars(select(Contact).order_by(Contact.name))
            ]
        }

    @app.get("/api/export")
    def export_data(session: Session = Depends(db)) -> Json:
        return {
            "jobs": [_job(row) for row in session.scalars(select(Job))],
            "contacts": [_contact(row) for row in session.scalars(select(Contact))],
        }

    @app.delete("/api/data")
    def delete_data(confirm: Literal["DELETE"], session: Session = Depends(db)) -> Json:
        counts = {
            "jobs": session.scalar(select(func.count(Job.id))) or 0,
            "contacts": session.scalar(select(func.count(Contact.id))) or 0,
        }
        for model in (
            OutreachEvent,
            Draft,
            AngleEvidence,
            ResearchAngle,
            ContactEmail,
            ContactEvidence,
            JobContact,
            JobSource,
            IngestMessage,
            PipelineRun,
            UsageCounter,
            Contact,
            Job,
        ):
            session.execute(delete(model))
        session.commit()
        return {"deleted": counts}

    dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str) -> Response:
            target = dist / path
            return FileResponse(target if target.is_file() else dist / "index.html")

    return app


app = create_app()

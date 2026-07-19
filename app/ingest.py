from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import canonical_url, job_keys
from app.models import IngestMessage, Job, JobSource


@dataclass(slots=True)
class JobInput:
    title: str
    company: str
    location: str = ""
    description: str = ""
    requisition_id: str | None = None
    url: str | None = None
    source: str = "manual"
    external_id: str | None = None
    posted_at: datetime | None = None
    metadata: dict[str, object] | None = None


@dataclass(slots=True)
class AlertMessage:
    external_id: str
    subject: str
    received_at: datetime | None
    text: str
    links: list[str]


def _after(lines: list[str], label: str) -> str:
    wanted = label.casefold()
    for index, line in enumerate(lines[:-1]):
        if line.casefold().rstrip(":") == wanted:
            return lines[index + 1]
    return ""


def parse_job_text(text: str, *, company: str = "", url: str | None = None) -> JobInput:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Job text is empty")
    title = _after(lines, "job title") or lines[0]
    location = _after(lines, "locations") or _after(lines, "location")
    requisition_id = _after(lines, "job requisition id") or None
    if not requisition_id:
        match = re.search(r"\b(?:JR|REQ)[-_ ]?\d{3,}\b", text, re.IGNORECASE)
        requisition_id = match.group(0) if match else None
    summary_match = re.search(
        r"\bJob Summary\b\s*(.+?)(?=\n\s*(?:Organizational Status|Work Performed)\b|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    description = " ".join(summary_match.group(1).split()) if summary_match else " ".join(lines)
    return JobInput(
        title=title,
        company=company or "Unknown company",
        location=location,
        description=description,
        requisition_id=requisition_id,
        url=url,
    )


def upsert_job(session: Session, incoming: JobInput) -> Job:
    keys = job_keys(
        source=incoming.source,
        external_id=incoming.external_id,
        requisition_id=incoming.requisition_id,
        company=incoming.company,
        title=incoming.title,
        location=incoming.location,
        url=incoming.url,
        description=incoming.description,
    )
    # ponytail: O(n) identity scan is appropriate for a local personal database;
    # promote keys to a join table when job volume makes this measurable.
    match: Job | None = None
    for candidate in session.scalars(select(Job)):
        if set(json.loads(candidate.identity_keys_json)) & set(keys):
            match = candidate
            break
    if match is None:
        match = Job(
            title=incoming.title,
            company=incoming.company,
            location=incoming.location,
            description=incoming.description,
            requisition_id=incoming.requisition_id,
            canonical_url=canonical_url(incoming.url),
            identity_keys_json=json.dumps(keys),
            posted_at=incoming.posted_at,
        )
        session.add(match)
        session.flush()
    else:
        merged_keys = sorted(set(json.loads(match.identity_keys_json)) | set(keys))
        match.identity_keys_json = json.dumps(merged_keys)
        existing_placeholder = match.title.casefold().startswith("linkedin job ")
        incoming_is_real = not incoming.title.casefold().startswith("linkedin job ")
        if existing_placeholder and incoming_is_real:
            match.title = incoming.title
        if incoming.description and len(incoming.description) > len(match.description):
            match.description = incoming.description
    existing_source = session.scalar(
        select(JobSource).where(
            JobSource.job_id == match.id,
            JobSource.source == incoming.source,
            JobSource.external_id == incoming.external_id,
        )
    )
    if existing_source is None:
        session.add(
            JobSource(
                job_id=match.id,
                source=incoming.source,
                external_id=incoming.external_id,
                source_url=canonical_url(incoming.url),
                metadata_json=json.dumps(incoming.metadata or {}),
            )
        )
    session.commit()
    return match


def parse_gmail_raw(external_id: str, raw: str) -> AlertMessage:
    padded = raw + "=" * (-len(raw) % 4)
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(padded))
    plain_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/plain":
            plain_parts.append(part.get_content())
        elif part.get_content_type() == "text/html":
            html_parts.append(part.get_content())
    html = "\n".join(html_parts)
    text = "\n".join(plain_parts) or BeautifulSoup(html, "html.parser").get_text(" ")
    raw_links = re.findall(r'https?://[^\s"<>\']+', f"{text}\n{html}")
    links: list[str] = []
    for raw_link in raw_links:
        clean = canonical_url(raw_link.rstrip(").,"))
        if clean and "linkedin.com/comm/jobs/view/" in clean:
            clean = clean.replace("/comm/jobs/view/", "/jobs/view/", 1)
        if clean and "linkedin.com/jobs/view/" in clean:
            clean = clean.split("?", 1)[0]
        if clean and "linkedin.com/jobs/view/" in clean and clean not in links:
            links.append(clean)
    received_at = None
    if message.get("date"):
        try:
            received_at = parsedate_to_datetime(message["date"])
        except (TypeError, ValueError):
            pass
    return AlertMessage(
        external_id=external_id,
        subject=str(message.get("subject", "")),
        received_at=received_at,
        text=text,
        links=links,
    )


def record_ingest_message(
    session: Session,
    source: str,
    external_id: str,
    subject: str = "",
    received_at: datetime | None = None,
) -> bool:
    existing = session.scalar(
        select(IngestMessage).where(
            IngestMessage.source == source,
            IngestMessage.external_id == external_id,
        )
    )
    if existing:
        return False
    session.add(
        IngestMessage(
            source=source,
            external_id=external_id,
            subject=subject,
            received_at=received_at,
        )
    )
    session.commit()
    return True

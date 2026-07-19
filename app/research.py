from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import canonical_url, contact_keys, normalize_text
from app.models import Contact, ContactEmail, ContactEvidence, Job, JobContact
from app.security import email_confidence


@dataclass(slots=True)
class ContactCandidate:
    name: str
    title: str
    company: str
    profile_url: str | None = None
    email: str | None = None
    score: float = 0
    rationale: str = ""


def rank_contacts(
    candidates: list[ContactCandidate],
    *,
    company: str,
    job_title: str,
    department: str = "",
) -> list[ContactCandidate]:
    target_company = normalize_text(company)
    target_words = set(normalize_text(f"{job_title} {department}").split())
    for candidate in candidates:
        title = normalize_text(candidate.title)
        score = 20 if normalize_text(candidate.company) == target_company else 0
        if any(word in title for word in ("manager", "director", "lead", "head")):
            score += 50
        if any(word in title for word in ("recruit", "talent acquisition", "human resources")):
            score += 35
        overlap = target_words & set(title.split())
        score += len(overlap) * 12
        if any(word in title for word in ("data", "software", "engineering")):
            score += 10
        candidate.score = score
        candidate.rationale = (
            f"Role relevance {score:.0f}: {candidate.title} at {candidate.company}."
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)[:3]


def _find_contact(session: Session, keys: list[str]) -> Contact | None:
    for candidate in session.scalars(select(Contact)):
        if set(json.loads(candidate.identity_keys_json or "[]")) & set(keys):
            return candidate
    return None


def save_recommendations(
    session: Session, job: Job, candidates: list[ContactCandidate]
) -> list[int]:
    ids: list[int] = []
    for rank, candidate in enumerate(candidates[:3], start=1):
        keys = contact_keys(
            name=candidate.name,
            company=candidate.company,
            email=candidate.email,
            profile_url=candidate.profile_url,
        )
        contact = _find_contact(session, keys)
        if contact is None:
            contact = Contact(
                name=candidate.name,
                title=candidate.title,
                company=candidate.company,
                profile_url=canonical_url(candidate.profile_url),
                identity_keys_json=json.dumps(keys),
            )
            session.add(contact)
            session.flush()
        link = session.scalar(
            select(JobContact).where(
                JobContact.job_id == job.id, JobContact.contact_id == contact.id
            )
        )
        if link is None:
            session.add(
                JobContact(
                    job_id=job.id,
                    contact_id=contact.id,
                    score=candidate.score,
                    rationale=candidate.rationale,
                    rank=rank,
                )
            )
        ids.append(contact.id)
    session.commit()
    return ids


def save_evidence(
    session: Session,
    contact: Contact,
    *,
    title: str,
    source_url: str,
    excerpt: str,
    kind: str = "professional",
) -> ContactEvidence:
    clean_excerpt = " ".join(excerpt.split())[:500]
    digest = hashlib.sha256(
        f"{canonical_url(source_url) or source_url}\n{clean_excerpt}".encode()
    ).hexdigest()
    existing = session.scalar(
        select(ContactEvidence).where(
            ContactEvidence.contact_id == contact.id,
            ContactEvidence.content_hash == digest,
        )
    )
    if existing:
        return existing
    evidence = ContactEvidence(
        contact_id=contact.id,
        kind=kind,
        title=title[:500],
        source_url=canonical_url(source_url) or source_url,
        excerpt=clean_excerpt,
        content_hash=digest,
    )
    session.add(evidence)
    session.commit()
    return evidence


def save_public_emails(
    session: Session,
    contact: Contact,
    *,
    text: str,
    source_url: str,
) -> list[ContactEmail]:
    source_host = (urlsplit(source_url).hostname or "").casefold()
    addresses = {
        match.casefold().rstrip(".,;:")
        for match in re.findall(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            text,
            re.IGNORECASE,
        )
    }
    rows: list[ContactEmail] = []
    for address in sorted(addresses):
        domain = address.rsplit("@", 1)[1]
        source_kind = (
            "official"
            if source_host == domain or source_host.endswith(f".{domain}")
            else "third_party"
        )
        row = session.scalar(
            select(ContactEmail).where(
                ContactEmail.contact_id == contact.id,
                ContactEmail.email == address,
            )
        )
        if row is None:
            row = ContactEmail(
                contact_id=contact.id,
                email=address,
                confidence=email_confidence(source_kind),
                source_url=canonical_url(source_url) or source_url,
            )
            session.add(row)
        rows.append(row)
    session.commit()
    return rows


def contact_queries(*, company: str, department: str, job_title: str) -> list[str]:
    clean_company = re.sub(r"\s+", " ", company).strip()
    clean_department = re.sub(r"\s+", " ", department).strip()
    role_area = " ".join(normalize_text(job_title).split()[:4])
    department_term = f'"{clean_department}" ' if clean_department else ""
    queries = [
        f'site:linkedin.com/in "{clean_company}" {department_term}manager',
        f'site:linkedin.com/in "{clean_company}" recruiter "talent acquisition"',
        f'-site:linkedin.com "{clean_company}" "{role_area}" team staff',
    ]
    return [query[:180] for query in queries if query.replace('"', "").strip()]

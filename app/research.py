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
) -> ContactEvidence | None:
    clean_excerpt = " ".join(excerpt.split())[:500]
    if evidence_rejection_reason(
        f"{title} {clean_excerpt}",
        person_name=contact.name,
        source_url=source_url,
    ):
        return None
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


def evidence_rejection_reason(
    text: str,
    *,
    person_name: str,
    source_url: str,
    organization: str = "",
) -> str | None:
    host = (urlsplit(source_url).hostname or "").casefold()
    normalized = normalize_text(text)
    normalized_name = normalize_text(person_name)
    name_parts = normalized_name.split()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "LinkedIn is a profile link, not a research source"
    shell_markers = (
        "agree join linkedin",
        "sign up linkedin",
        "sign in to see",
        "user agreement privacy policy cookie policy",
    )
    if any(marker in normalized for marker in shell_markers):
        return "Authentication or consent shell"
    if len(normalized.split()) < 20:
        return "Page has too little substantive content"
    if normalized_name not in normalized and (
        not name_parts or name_parts[-1] not in normalized.split()
    ):
        return "Page does not identify the selected person"
    if organization:
        normalized_organization = normalize_text(organization)
        alias = normalize_text(_company_alias(organization))
        if (
            normalized_organization not in normalized
            and alias not in normalized.split()
            and alias not in normalize_text(host)
        ):
            return "Page does not confirm the selected organization"
    return None


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


def _company_alias(company: str) -> str:
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9]+", company)
        if word.casefold() not in {"the", "of", "and"}
    ]
    initials = "".join(word[0] for word in words).upper()
    return initials if len(words) >= 3 and 2 <= len(initials) <= 6 else company


def _contact_context(description: str, department: str) -> tuple[str, str]:
    manager_unit = re.search(
        r"\breport(?:s|ing)? to (?:the )?(?:manager|director|lead)[,:]?\s+"
        r"([^,.;\n]{3,80})",
        description,
        re.IGNORECASE,
    )
    focus = department.strip() or (manager_unit.group(1).strip() if manager_unit else "")
    product = re.search(r"\b[A-Z][A-Za-z-]*\d+[A-Za-z0-9-]*\b", description)
    return focus, product.group(0) if product else ""


def contact_focus_terms(description: str, department: str = "") -> list[str]:
    return [term for term in _contact_context(description, department) if term]


def contact_choice_rejection_reason(
    *,
    name: str,
    title: str,
    source_text: str,
    focus_terms: list[str],
) -> str | None:
    name_parts = normalize_text(name).split()
    if len(name_parts) < 2 or any(len(part) < 2 for part in name_parts):
        return "Selected person does not have a sufficiently specific public name"
    normalized_title = normalize_text(title)
    is_recruiter = any(
        term in normalized_title
        for term in ("recruit", "talent acquisition", "human resources")
    )
    normalized_source = normalize_text(source_text)
    if focus_terms and not is_recruiter and not any(
        normalize_text(term) in normalized_source for term in focus_terms
    ):
        return "Selected role is not tied to the job's unit or platform"
    return None


def grounded_contact_title(*, name: str, proposed: str, source_text: str) -> str | None:
    if normalize_text(proposed) in normalize_text(source_text):
        return proposed.strip()
    first_name = re.escape(name.split()[0]) if name.split() else ""
    if not first_name:
        return None
    role = re.search(
        rf"\b{first_name}\s+(?:is|serves as|works as)\s+(?:an?\s+|the\s+)?"
        r"(.{3,120}?)(?=\s+(?:with|who|at|for)\b|[.;])",
        source_text,
        re.IGNORECASE,
    )
    if not role:
        return None
    value = " ".join(role.group(1).split()).strip(" ,-")
    return value[:1].upper() + value[1:100] if value else None


def relevant_excerpt(text: str, terms: list[str], *, limit: int = 3_000) -> str:
    clean = " ".join(text.split())
    windows: list[str] = []
    folded = clean.casefold()
    for term in terms:
        index = folded.find(term.casefold())
        if index >= 0:
            windows.append(clean[max(0, index - 700) : index + 1_400])
    return ("\n".join(dict.fromkeys(windows)) if windows else clean)[:limit]


def contact_queries(
    *,
    company: str,
    department: str,
    job_title: str,
    description: str = "",
) -> list[str]:
    clean_company = re.sub(r"\s+", " ", company).strip()
    clean_department, product = _contact_context(description, department)
    clean_department = re.sub(r"\s+", " ", clean_department).strip()
    role_area = " ".join(normalize_text(job_title).split()[:4])
    focus = clean_department or role_area
    alias = _company_alias(clean_company)
    product_term = f"{product} " if product else ""
    queries = [
        f'{alias} {product_term}"{focus}" team',
        f'site:linkedin.com/in "{clean_company}" "{focus}"',
        f'"{clean_company}" "{focus}" manager director -site:linkedin.com',
    ]
    return [query[:180] for query in queries if query.replace('"', "").strip()]


def person_research_queries(*, name: str, company: str) -> list[str]:
    clean_name = re.sub(r"\s+", " ", name).strip()
    clean_company = re.sub(r"\s+", " ", company).strip()
    return [
        f'"{clean_name}" "{clean_company}" bio team -site:linkedin.com',
        f'"{clean_name}" publication research project -site:linkedin.com',
        f'"{clean_name}" interview talk product -site:linkedin.com',
    ]

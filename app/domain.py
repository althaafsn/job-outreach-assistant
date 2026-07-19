from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "trk",
    "trackingid",
}


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = re.sub(r"[\u2010-\u2015_/|]+", " ", text)
    text = re.sub(r"[^\w]+", " ", text)
    return " ".join(text.split())


def canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.casefold()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    if (
        port
        and not (parts.scheme.casefold() == "http" and port == 80)
        and not (parts.scheme.casefold() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), host, path, urlencode(sorted(query)), "")).rstrip(
        "/"
    )


def job_keys(
    *,
    source: str,
    external_id: str | None,
    requisition_id: str | None,
    company: str,
    title: str,
    location: str,
    url: str | None,
    description: str,
) -> list[str]:
    keys: list[str] = []
    company_key = normalize_text(company)
    if external_id:
        keys.append(f"source:{normalize_text(source)}:{normalize_text(external_id)}")
    if requisition_id and company_key:
        keys.append(f"req:{company_key}:{normalize_text(requisition_id)}")
    if canonical := canonical_url(url):
        keys.append(f"url:{canonical}")
    if keys:
        return keys
    digest = hashlib.sha256(normalize_text(description).encode()).hexdigest()[:16]
    return [f"content:{company_key}:{normalize_text(title)}:{normalize_text(location)}:{digest}"]


def contact_keys(
    *,
    name: str,
    company: str,
    email: str | None = None,
    profile_url: str | None = None,
) -> list[str]:
    keys: list[str] = []
    if email:
        keys.append(f"email:{email.strip().casefold()}")
    if canonical := canonical_url(profile_url):
        keys.append(f"profile:{canonical}")
    keys.append(f"person:{normalize_text(name)}:{normalize_text(company)}")
    return keys

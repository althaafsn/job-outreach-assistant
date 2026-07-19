from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

Resolver = Callable[..., list[tuple[Any, ...]]]


class UnsafeURL(ValueError):
    pass


class FetchRejected(RuntimeError):
    pass


def _require_public_ip(raw: str) -> None:
    ip = ipaddress.ip_address(raw)
    if not ip.is_global:
        raise UnsafeURL(f"Destination is not public: {ip}")


def validate_public_url(url: str, *, resolver: Resolver = socket.getaddrinfo) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme.casefold() == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("Malformed URL") from exc
    if parts.scheme.casefold() not in {"http", "https"} or not host:
        raise UnsafeURL("Only public HTTP(S) URLs are allowed")
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        _require_public_ip(host)
        return url
    try:
        addresses = resolver(host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL("Hostname could not be resolved") from exc
    if not addresses:
        raise UnsafeURL("Hostname did not resolve")
    for result in addresses:
        _require_public_ip(result[4][0])
    return url


class SafeFetcher:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = socket.getaddrinfo,
        max_bytes: int = 1_000_000,
        timeout: float = 10,
    ) -> None:
        self.resolver = resolver
        self.max_bytes = max_bytes
        self.client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": "JobOutreachResearchBot/0.1 (+local single-user tool)"},
        )

    def get_text(self, url: str) -> str:
        current = url
        for _ in range(4):
            validate_public_url(current, resolver=self.resolver)
            with self.client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchRejected("Redirect has no destination")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").casefold()
                if not (
                    content_type.startswith("text/")
                    or "application/json" in content_type
                    or "application/xhtml+xml" in content_type
                ):
                    raise FetchRejected(f"Unsupported content type: {content_type or 'unknown'}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise FetchRejected("Response exceeds size limit")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace")
        raise FetchRejected("Too many redirects")

    def close(self) -> None:
        self.client.close()


def email_confidence(source_kind: str, same_domain_examples: int = 0) -> str:
    return {
        "official": "verified_public_official",
        "publication": "verified_public_publication",
        "third_party": "public_third_party",
    }.get(
        source_kind,
        "inferred_pattern"
        if source_kind == "inferred" and same_domain_examples >= 2
        else "unverified",
    )

from __future__ import annotations

import importlib
import socket

import httpx
import pytest


def _security():
    try:
        return importlib.import_module("app.security")
    except ModuleNotFoundError:
        return None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "http://10.2.3.4/",
        "http://192.168.1.1/",
    ],
)
def test_rejects_non_public_destinations(url: str) -> None:
    security = _security()
    assert security is not None
    with pytest.raises(security.UnsafeURL):
        security.validate_public_url(url)


def test_rejects_hostname_that_resolves_to_private_address() -> None:
    security = _security()
    assert security is not None

    def private_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 0))]

    with pytest.raises(security.UnsafeURL):
        security.validate_public_url("https://public-looking.example/page", resolver=private_dns)


def test_fetcher_rechecks_redirect_destination() -> None:
    security = _security()
    assert security is not None

    def public_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    )
    fetcher = security.SafeFetcher(transport=transport, resolver=public_dns)
    with pytest.raises(security.UnsafeURL):
        fetcher.get_text("https://example.org/profile")


def test_fetcher_accepts_bounded_public_html() -> None:
    security = _security()
    assert security is not None

    def public_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><body>Public profile</body></html>",
        )
    )
    fetcher = security.SafeFetcher(transport=transport, resolver=public_dns)
    assert "Public profile" in fetcher.get_text("https://example.org/profile")


def test_fetcher_rejects_binary_and_oversized_responses() -> None:
    security = _security()
    assert security is not None

    def public_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    binary = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"x"
        )
    )
    with pytest.raises(security.FetchRejected):
        security.SafeFetcher(transport=binary, resolver=public_dns).get_text(
            "https://example.org/file"
        )

    large = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"x" * 33
        )
    )
    with pytest.raises(security.FetchRejected):
        security.SafeFetcher(transport=large, resolver=public_dns, max_bytes=32).get_text(
            "https://example.org/file"
        )


@pytest.mark.parametrize(
    ("source_kind", "same_domain_examples", "expected"),
    [
        ("official", 0, "verified_public_official"),
        ("publication", 0, "verified_public_publication"),
        ("third_party", 0, "public_third_party"),
        ("inferred", 2, "inferred_pattern"),
        ("inferred", 1, "unverified"),
        ("unknown", 0, "unverified"),
    ],
)
def test_email_confidence_requires_public_evidence(
    source_kind: str, same_domain_examples: int, expected: str
) -> None:
    security = _security()
    assert security is not None
    assert security.email_confidence(source_kind, same_domain_examples) == expected

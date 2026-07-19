from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from app.db import create_schema, make_engine, make_session_factory
from app.models import UsageCounter
from app.security import FetchRejected, SafeFetcher


def _module():
    try:
        from app import integrations

        return integrations
    except ImportError:
        return None


def _session(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'integrations.db'}")
    create_schema(engine)
    return make_session_factory(engine)()


def test_brave_search_does_not_apply_an_application_daily_cap(
    tmp_path: Path,
) -> None:
    integrations = _module()
    assert integrations is not None
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "<strong>Ada</strong> Lovelace - Research Data Manager",
                            "url": "https://example.edu/people/ada",
                            "description": (
                                "<strong>Ada</strong> leads research data services "
                                "at Example University."
                            ),
                            "profile": {"ignored": ["large"]},
                        }
                    ]
                }
            },
        )

    with _session(tmp_path) as session:
        client = integrations.BraveSearchClient(
            api_key="key",
            transport=httpx.MockTransport(handler),
        )
        results = client.search('"Example University" research data manager')
        assert results == [
            integrations.SearchResult(
                title="Ada Lovelace - Research Data Manager",
                url="https://example.edu/people/ada",
                snippet="Ada leads research data services at Example University.",
            )
        ]
        assert "q=" in str(seen[0].url)
        assert seen[0].headers["x-subscription-token"] == "key"
        assert client.search("another query") == results
        assert session.query(UsageCounter).count() == 0


def test_brave_search_requires_its_own_credentials(tmp_path: Path) -> None:
    integrations = _module()
    assert integrations is not None
    with _session(tmp_path):
        client = integrations.BraveSearchClient(api_key="")
        with pytest.raises(integrations.DeferredIntegration):
            client.search("query")


def test_public_page_reader_uses_direct_page_without_jina() -> None:
    integrations = _module()
    assert integrations is not None
    seen: list[str] = []

    def public_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><nav>Menu</nav>"
                "<main>Ada leads public research data services.</main></html>"
            ),
        )

    fetcher = SafeFetcher(transport=httpx.MockTransport(handler), resolver=public_dns)
    assert integrations.read_public_page("https://example.edu/ada", fetcher=fetcher) == (
        "Menu Ada leads public research data services."
    )
    assert seen == ["https://example.edu/ada"]


def test_public_page_reader_falls_back_to_jina_after_direct_failure() -> None:
    integrations = _module()
    assert integrations is not None
    seen: list[str] = []

    def public_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "example.edu":
            return httpx.Response(403, headers={"content-type": "text/html"})
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="# Ada Lovelace\n\nAda leads public research data services.",
        )

    fetcher = SafeFetcher(transport=httpx.MockTransport(handler), resolver=public_dns)
    assert "Ada leads public research" in integrations.read_public_page(
        "https://example.edu/ada", fetcher=fetcher
    )
    assert seen == [
        "https://example.edu/ada",
        "https://r.jina.ai/https://example.edu/ada",
    ]


def test_public_page_reader_falls_back_when_direct_page_is_a_login_shell() -> None:
    integrations = _module()
    assert integrations is not None
    seen: list[str] = []

    def public_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "example.edu":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><main>Sign in to LinkedIn or join now to view this job.</main></html>",
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="# Data Engineer\n\nBuild secure pipelines for research data.",
        )

    fetcher = SafeFetcher(transport=httpx.MockTransport(handler), resolver=public_dns)
    assert "Build secure pipelines" in integrations.read_public_page(
        "https://example.edu/job", fetcher=fetcher
    )
    assert seen == [
        "https://example.edu/job",
        "https://r.jina.ai/https://example.edu/job",
    ]


def test_public_page_reader_rejects_login_shell_returned_by_jina() -> None:
    integrations = _module()
    assert integrations is not None

    def public_dns(*_args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "linkedin.com":
            return httpx.Response(403, headers={"content-type": "text/html"})
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text=(
                "Title: Sign Up | LinkedIn\n"
                "Agree & Join LinkedIn. Sign in or join now to continue. "
                "User Agreement Privacy Policy Cookie Policy."
            ),
        )

    fetcher = SafeFetcher(transport=httpx.MockTransport(handler), resolver=public_dns)
    with pytest.raises(FetchRejected, match="authentication or consent shell"):
        integrations.read_public_page(
            "https://linkedin.com/in/ada-lovelace",
            fetcher=fetcher,
        )


def test_gmail_reader_paginates_and_requests_raw_messages_only() -> None:
    integrations = _module()
    assert integrations is not None

    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Messages:
        def __init__(self):
            self.get_calls = []
            self.list_calls = []
            self.batch_calls = 0

        def list(self, **kwargs):
            self.list_calls.append(kwargs)
            token = kwargs.get("pageToken")
            return Request(
                {"messages": [{"id": "m1"}], "nextPageToken": "next"}
                if token is None
                else {"messages": [{"id": "m2"}]}
            )

        def get(self, **kwargs):
            self.get_calls.append(kwargs)
            return Request({"id": kwargs["id"], "raw": "cmF3"})

        def batch(self):
            self.batch_calls += 1
            return Batch()

    class Batch:
        def __init__(self):
            self.requests = []

        def add(self, request, request_id):
            self.requests.append((request, request_id))

        def execute(self):
            for request, request_id in self.requests:
                callback_holder["callback"](request_id, request.execute(), None)

    messages = Messages()

    class Users:
        def messages(self):
            return messages

    class Service:
        def users(self):
            return Users()

        def new_batch_http_request(self, callback):
            callback_holder["callback"] = callback
            return messages.batch()

    callback_holder = {}
    rows = list(integrations.iter_gmail_raw(Service(), query="newer_than:180d"))
    assert [row["id"] for row in rows] == ["m1", "m2"]
    assert all(call["maxResults"] == 20 for call in messages.list_calls)
    assert all(call["format"] == "raw" for call in messages.get_calls)
    assert messages.batch_calls == 2

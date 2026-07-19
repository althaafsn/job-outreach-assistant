from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.db import create_schema, make_engine, make_session_factory


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


def test_google_search_uses_quota_and_returns_small_public_result_shape(
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
                "items": [
                    {
                        "title": "Ada Lovelace - Research Data Manager",
                        "link": "https://example.edu/people/ada",
                        "snippet": "Ada leads research data services at Example University.",
                        "pagemap": {"ignored": ["large"]},
                    }
                ]
            },
        )

    with _session(tmp_path) as session:
        client = integrations.GoogleSearchClient(
            api_key="key",
            engine_id="cx",
            session=session,
            daily_limit=1,
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
        with pytest.raises(integrations.DeferredIntegration):
            client.search("another query")


def test_google_search_requires_its_own_credentials(tmp_path: Path) -> None:
    integrations = _module()
    assert integrations is not None
    with _session(tmp_path) as session:
        client = integrations.GoogleSearchClient(api_key="", engine_id="", session=session)
        with pytest.raises(integrations.DeferredIntegration):
            client.search("query")


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

        def list(self, **kwargs):
            token = kwargs.get("pageToken")
            return Request(
                {"messages": [{"id": "m1"}], "nextPageToken": "next"}
                if token is None
                else {"messages": [{"id": "m2"}]}
            )

        def get(self, **kwargs):
            self.get_calls.append(kwargs)
            return Request({"id": kwargs["id"], "raw": "cmF3"})

    messages = Messages()

    class Users:
        def messages(self):
            return messages

    class Service:
        def users(self):
            return Users()

    rows = list(integrations.iter_gmail_raw(Service(), query="newer_than:180d"))
    assert [row["id"] for row in rows] == ["m1", "m2"]
    assert all(call["format"] == "raw" for call in messages.get_calls)

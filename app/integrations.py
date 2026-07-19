from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.security import FetchRejected, SafeFetcher, validate_public_url

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


class DeferredIntegration(RuntimeError):
    pass


@dataclass(slots=True, eq=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def _plain(value: object, limit: int) -> str:
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)[:limit]


class BraveSearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.client = httpx.Client(
            base_url="https://api.search.brave.com",
            transport=transport,
            timeout=20,
            headers={"X-Subscription-Token": api_key},
        )

    def search(self, query: str, *, start: int = 0) -> list[SearchResult]:
        if not self.api_key:
            raise DeferredIntegration("Brave Search is not configured")
        response = self.client.get(
            "/res/v1/web/search",
            params={
                "q": query[:180],
                "offset": max(0, min(start, 9)),
                "count": 10,
                "country": "ca",
                "search_lang": "en",
            },
        )
        if response.status_code in {401, 402, 403, 429}:
            raise DeferredIntegration("Brave search quota, credentials, or rate limit reached")
        response.raise_for_status()
        return [
            SearchResult(
                title=_plain(item.get("title", ""), 500),
                url=str(item.get("url", ""))[:1000],
                snippet=_plain(item.get("description", ""), 1000),
            )
            for item in response.json().get("web", {}).get("results", [])
            if item.get("url")
        ]


def read_public_page(url: str, *, fetcher: SafeFetcher | None = None) -> str:
    own_fetcher = fetcher is None
    fetcher = fetcher or SafeFetcher()
    try:
        validate_public_url(url, resolver=fetcher.resolver)
        try:
            raw = fetcher.get_text(url)
        except (FetchRejected, httpx.HTTPError):
            raw = fetcher.get_text(f"https://r.jina.ai/{url}")
        soup = BeautifulSoup(raw, "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()
        return soup.get_text(" ", strip=True)[:50_000]
    finally:
        if own_fetcher:
            fetcher.close()


def gmail_credentials(
    credentials_file: Path, token_file: Path, *, interactive: bool
) -> Credentials:
    credentials: Credentials | None = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
            str(token_file), [GMAIL_READONLY]
        )
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())  # type: ignore[no-untyped-call]
    elif not credentials or not credentials.valid:
        if not interactive:
            raise DeferredIntegration("Gmail authorization is required")
        if not credentials_file.exists():
            raise DeferredIntegration(f"Gmail OAuth client file is missing: {credentials_file}")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), [GMAIL_READONLY])
        credentials = flow.run_local_server(port=0)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def gmail_service(credentials_file: Path, token_file: Path, *, interactive: bool) -> Any:
    from googleapiclient.discovery import build

    credentials = gmail_credentials(credentials_file, token_file, interactive=interactive)
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def iter_gmail_raw(service: Any, *, query: str) -> Iterator[dict[str, str]]:
    token: str | None = None
    while True:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=token, maxResults=20)
            .execute()
        )
        message_ids = [item["id"] for item in response.get("messages", [])]
        new_batch = getattr(service, "new_batch_http_request", None)
        if callable(new_batch) and message_ids:
            results: dict[str, dict[str, str]] = {}
            errors: list[Exception] = []

            def callback(
                request_id: str,
                result: dict[str, str],
                exception: Exception | None,
                *,
                _errors: list[Exception] = errors,
                _results: dict[str, dict[str, str]] = results,
            ) -> None:
                if exception is not None:
                    _errors.append(exception)
                else:
                    _results[request_id] = result

            batch = new_batch(callback=callback)
            for message_id in message_ids:
                batch.add(
                    service.users().messages().get(userId="me", id=message_id, format="raw"),
                    request_id=message_id,
                )
            batch.execute()
            if errors:
                raise errors[0]
            for message_id in message_ids:
                yield results[message_id]
        else:
            for message_id in message_ids:
                yield service.users().messages().get(
                    userId="me", id=message_id, format="raw"
                ).execute()
        token = response.get("nextPageToken")
        if not token:
            break

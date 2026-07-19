from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from sqlalchemy.orm import Session

from app.quotas import reserve

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


class DeferredIntegration(RuntimeError):
    pass


@dataclass(slots=True, eq=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class GoogleSearchClient:
    def __init__(
        self,
        *,
        api_key: str,
        engine_id: str,
        session: Session,
        daily_limit: int = 80,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.engine_id = engine_id
        self.session = session
        self.daily_limit = daily_limit
        self.client = httpx.Client(
            base_url="https://customsearch.googleapis.com",
            transport=transport,
            timeout=20,
        )

    def search(self, query: str, *, start: int = 1) -> list[SearchResult]:
        if not self.api_key or not self.engine_id:
            raise DeferredIntegration("Google Custom Search is not configured")
        if not reserve(self.session, "google_search", self.daily_limit):
            raise DeferredIntegration("Daily Google search budget is exhausted")
        response = self.client.get(
            "/customsearch/v1",
            params={
                "key": self.api_key,
                "cx": self.engine_id,
                "q": query[:180],
                "start": max(1, min(start, 91)),
                "num": 10,
            },
        )
        if response.status_code in {403, 429}:
            raise DeferredIntegration("Google search quota or rate limit reached")
        response.raise_for_status()
        return [
            SearchResult(
                title=str(item.get("title", ""))[:500],
                url=str(item.get("link", ""))[:1000],
                snippet=str(item.get("snippet", ""))[:1000],
            )
            for item in response.json().get("items", [])
            if item.get("link")
        ]


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
            .list(userId="me", q=query, pageToken=token, maxResults=100)
            .execute()
        )
        for item in response.get("messages", []):
            yield (
                service.users().messages().get(userId="me", id=item["id"], format="raw").execute()
            )
        token = response.get("nextPageToken")
        if not token:
            break

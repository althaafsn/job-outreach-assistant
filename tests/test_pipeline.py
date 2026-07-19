from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import create_schema, make_engine, make_session_factory
from app.ingest import JobInput, upsert_job
from app.integrations import SearchResult
from app.models import ContactEmail, ContactEvidence, IngestMessage, Job, PipelineRun


def _module():
    try:
        from app import pipeline

        return pipeline
    except ImportError:
        return None


def _factory(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    create_schema(engine)
    return make_session_factory(engine)


def test_daily_run_records_success_and_prevents_overlap(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)
    lock_path = tmp_path / "daily.lock"
    assert pipeline.run_steps(factory, lock_path, [("one", lambda _session: 2)]) == {"one": 2}
    with factory() as session:
        run = session.scalar(select(PipelineRun))
        assert run is not None
        assert run.status == "completed"
        assert run.finished_at is not None

    with pipeline.pipeline_lock(lock_path):
        with pytest.raises(pipeline.AlreadyRunning):
            pipeline.run_steps(factory, lock_path, [])


def test_gmail_title_extraction_ignores_tracking_query_parameters() -> None:
    pipeline = _module()
    assert pipeline is not None
    title = pipeline._title_before_link(
        "Data Engineer https://www.linkedin.com/comm/jobs/view/123?trackingId=email",
        "https://linkedin.com/jobs/view/123?refId=email",
    )
    assert title == "Data Engineer"


def test_daily_run_records_failure_without_swallowing_it(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)

    def broken(_session):
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic failure"):
        pipeline.run_steps(factory, tmp_path / "daily.lock", [("broken", broken)])
    with factory() as session:
        run = session.scalar(select(PipelineRun))
        assert run is not None
        assert run.status == "failed"
        assert "synthetic failure" in (run.error or "")


def test_daily_run_records_quota_deferral_without_losing_progress(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)

    def deferred(_session):
        raise pipeline.DeferredIntegration("Daily Brave search budget is exhausted")

    with pytest.raises(pipeline.DeferredIntegration):
        pipeline.run_steps(
            factory,
            tmp_path / "daily.lock",
            [("complete_first", lambda _session: 2), ("deferred", deferred)],
        )
    with factory() as session:
        run = session.scalar(select(PipelineRun))
        assert run is not None
        assert run.status == "deferred"
        assert run.counters_json == '{"complete_first": 2}'
        assert "budget is exhausted" in (run.error or "")


def test_gmail_ingest_keeps_metadata_not_full_message_body(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)
    mime = (
        "Subject: Data job alert\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Software Developer https://linkedin.com/jobs/view/123\r\n"
        "PRIVATE BODY THAT MUST NOT BE STORED"
    )
    raw = base64.urlsafe_b64encode(mime.encode()).decode().rstrip("=")

    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Messages:
        def list(self, **_kwargs):
            return Request({"messages": [{"id": "m1"}]})

        def get(self, **_kwargs):
            return Request({"id": "m1", "raw": raw})

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    with factory() as session:
        assert pipeline.ingest_gmail(session, Service(), query="newer_than:180d") == 1
        message = session.scalar(select(IngestMessage))
        assert message is not None
        assert "PRIVATE BODY" not in (message.subject or "")
        job = session.scalar(select(Job))
        assert job is not None
        assert job.canonical_url == "https://linkedin.com/jobs/view/123"
        assert "PRIVATE BODY" not in job.description


def test_gmail_ingest_retries_messages_without_recognized_job_links(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)
    mime = "Subject: Unrecognized alert\r\nContent-Type: text/plain\r\n\r\nNo job URL"
    raw = base64.urlsafe_b64encode(mime.encode()).decode().rstrip("=")

    class Request:
        def execute(self):
            return self.value

        def __init__(self, value):
            self.value = value

    class Messages:
        def list(self, **_kwargs):
            return Request({"messages": [{"id": "m-no-job"}]})

        def get(self, **_kwargs):
            return Request({"id": "m-no-job", "raw": raw})

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    with factory() as session:
        assert pipeline.ingest_gmail(session, Service(), query="alerts") == 0
        assert session.scalar(select(IngestMessage)) is None


def test_contact_research_reads_selected_public_pages(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)

    class Search:
        def search(self, _query: str) -> list[SearchResult]:
            return [
                SearchResult(
                    title="Ada Lovelace - Manager, Research Data Services",
                    url="https://example.edu/people/ada",
                    snippet="Short search snippet.",
                )
            ]

    with factory() as session:
        job = upsert_job(
            session,
            JobInput(
                title="Junior Data Coordinator",
                company="Example University",
                description="Support research data services.",
            ),
        )
        assert (
            pipeline.research_job(
                session,
                job,
                Search(),
                read_page=lambda _url: (
                    "Ada leads a public research data training program. "
                    "Contact ada@example.edu."
                ),
            )
            == 1
        )
        evidence = session.scalar(select(ContactEvidence))
        assert evidence is not None
        assert "training program" in evidence.excerpt
        email = session.scalar(select(ContactEmail))
        assert email is not None
        assert email.email == "ada@example.edu"

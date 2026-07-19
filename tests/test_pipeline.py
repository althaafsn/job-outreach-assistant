from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import create_schema, make_engine, make_session_factory
from app.ingest import JobInput, upsert_job
from app.integrations import SearchResult
from app.models import ContactEmail, ContactEvidence, IngestMessage, Job, JobSource, PipelineRun


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
    from app.ai import ContactSelection, Generated

    class Search:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str) -> list[SearchResult]:
            self.queries.append(query)
            if query.startswith('site:linkedin.com') or "team staff" in query:
                return [
                    SearchResult(
                        title="Ada Lovelace - Manager, Research Data Services | LinkedIn",
                        url="https://linkedin.com/in/ada",
                        snippet=(
                            "Ada Lovelace manages Research Data Services "
                            "at Example University."
                        ),
                    )
                ]
            return [
                SearchResult(
                    title="Ada Lovelace leads the Open Records Project",
                    url="https://example.edu/news/open-records",
                    snippet=(
                        "Ada Lovelace explains how the Open Records Project makes "
                        "validated public research data reusable."
                    ),
                ),
                SearchResult(
                    title="Ada Lovelace interview on research data stewardship",
                    url="https://example.org/interviews/ada",
                    snippet="Ada Lovelace discusses access, validation, and privacy.",
                ),
                SearchResult(
                    title="Ada Lovelace | LinkedIn",
                    url="https://linkedin.com/in/ada",
                    snippet="Sign in or join now.",
                ),
                SearchResult(
                    title="Ada Lovelace presents practical data validation",
                    url="https://conference.example.edu/speakers/ada",
                    snippet="Ada Lovelace presents lessons from practical validation work.",
                ),
                SearchResult(
                    title="Ada Lovelace joins a reproducible research panel",
                    url="https://events.example.org/reproducible-research",
                    snippet="Ada Lovelace joins a public panel on reproducible research.",
                ),
            ]

    class AI:
        def __init__(self) -> None:
            self.allowed: set[int] = set()

        def select_contacts(self, _prompt: str, *, allowed_result_ids: set[int]):
            self.allowed = allowed_result_ids
            return Generated(
                value=ContactSelection.model_validate(
                    {
                        "contacts": [
                            {
                                "result_id": min(allowed_result_ids),
                                "name": "Ada Lovelace",
                                "title": "Manager, Research Data Services",
                                "company": "Example University",
                                "rationale": "Leads the data team named in the posting.",
                            }
                        ]
                    }
                ),
                model="example/free",
            )

    pages = {
        "https://example.edu/news/open-records": (
            "Ada Lovelace leads the Open Records Project at Example University. "
            "The project standardizes public research records and publishes reusable "
            "documentation so community partners can audit and extend the datasets. "
            "Lovelace explains how the team balances access, privacy, and validation. "
            "Contact ada@example.edu."
        ),
        "https://example.org/interviews/ada": (
            "In this public interview, Ada Lovelace discusses research data stewardship. "
            "She describes lessons from training researchers, introducing validation "
            "checks, and designing secure workflows that remain practical for users. "
            "The discussion focuses on how feedback changed the team's approach."
        ),
        "https://conference.example.edu/speakers/ada": (
            "Ada Lovelace presents practical data validation methods for research teams. "
            "Her talk explains how small automated checks can expose inconsistent "
            "records before they enter shared repositories, and how clear documentation "
            "helps nontechnical collaborators resolve the underlying issues."
        ),
        "https://events.example.org/reproducible-research": (
            "Ada Lovelace joins a public panel on reproducible research and open data. "
            "The speakers compare approaches to audit trails, versioned datasets, "
            "privacy reviews, and documentation that enables other teams to reproduce "
            "published findings without repeating the original extraction work."
        ),
    }
    progress: list[dict] = []
    search = Search()
    ai = AI()
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
                search,
                ai,
                read_page=lambda url: pages[url],
                progress=progress.append,
            )
            == 1
        )
        evidence = session.scalars(select(ContactEvidence)).all()
        assert len(evidence) == 3
        assert not any(
            row.source_url == "https://events.example.org/reproducible-research"
            for row in evidence
        )
        assert all("linkedin.com" not in row.source_url for row in evidence)
        assert any("Open Records Project" in row.excerpt for row in evidence)
        email = session.scalar(select(ContactEmail))
        assert email is not None
        assert email.email == "ada@example.edu"
        assert len(search.queries) == 6
        assert ai.allowed
        assert any(event.get("event") == "search" for event in progress)
        assert any(
            event.get("event") == "source"
            and event.get("decision") == "rejected"
            and event.get("reason") == "LinkedIn is a profile link, not a research source"
            for event in progress
        )
        assert any(
            event.get("event") == "source" and event.get("decision") == "accepted"
            for event in progress
        )


def _job_body() -> str:
    sentence = (
        "Build reliable data pipelines, test production services, document decisions, "
        "and collaborate with researchers and software developers. "
    )
    return sentence * 8


class ExtractionAI:
    def __init__(self, output):
        self.output = output

    def extract_job(self, _prompt: str):
        from app.ai import Generated

        return Generated(value=self.output, model="example/free")


def _extraction(page_type: str = "individual_job", *, text: str | None = None):
    from app.ai import JobExtraction

    return JobExtraction.model_validate(
        {
            "page_type": page_type,
            "title": "Data Engineer" if page_type == "individual_job" else "",
            "company": "Example Health" if page_type == "individual_job" else "",
            "location": "Vancouver, BC",
            "requisition_id": "JR12345",
            "posted_at": "2026-07-18",
            "sections": (
                [{"heading": "Responsibilities", "text": text or _job_body()}]
                if page_type == "individual_job"
                else []
            ),
            "reason": "This page lists multiple jobs." if page_type != "individual_job" else "",
        }
    )


def test_verified_extraction_updates_content_but_preserves_user_workflow(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)
    body = _job_body()
    source = f"Data Engineer Example Health Vancouver, BC Responsibilities {body}"
    with factory() as session:
        job = upsert_job(
            session,
            JobInput(
                title="Needs processing",
                company="Needs review",
                description=source,
                source="manual",
                external_id="manual-1",
            ),
        )
        job.status = "interested"
        job.notes = "Strong match"
        session.commit()

        result = pipeline.extract_job(session, job, ExtractionAI(_extraction()))

        assert result.quality_status == "verified"
        assert result.title == "Data Engineer"
        assert result.company == "Example Health"
        assert result.location == "Vancouver, BC"
        assert result.status == "interested"
        assert result.notes == "Strong match"
        assert result.extraction_model == "example/free"
        assert result.extracted_at is not None


def test_collection_extraction_is_rejected_without_overwriting_discovery_data(
    tmp_path: Path,
) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)
    with factory() as session:
        job = upsert_job(
            session,
            JobInput(
                title="Data jobs in Canada",
                company="Needs review",
                description="Search results",
                url="https://example.com/jobs",
                source="brave",
                external_id="https://example.com/jobs",
            ),
        )
        result = pipeline.extract_job(
            session,
            job,
            ExtractionAI(_extraction("collection")),
            read_page=lambda _url: "Many data jobs in Canada",
        )
        assert result.quality_status == "rejected"
        assert result.title == "Data jobs in Canada"
        assert "multiple jobs" in (result.extraction_error or "")


def test_ungrounded_extraction_needs_review(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    factory = _factory(tmp_path)
    with factory() as session:
        job = upsert_job(
            session,
            JobInput(
                title="Needs processing",
                company="Needs review",
                description="Data Engineer Example Health but no job duties.",
                source="manual",
                external_id="manual-2",
            ),
        )
        result = pipeline.extract_job(session, job, ExtractionAI(_extraction()))
        assert result.quality_status == "needs_review"
        assert "section" in (result.extraction_error or "")
        assert result.extraction_attempts == 1


def test_quota_deferral_leaves_job_pending(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    from app.ai import DeferredAI

    class DeferredClient:
        def extract_job(self, _prompt: str):
            raise DeferredAI("Daily OpenRouter request budget is exhausted")

    factory = _factory(tmp_path)
    with factory() as session:
        job = upsert_job(
            session,
            JobInput(
                title="Needs processing",
                company="Needs review",
                description="Full manual posting",
                source="manual",
                external_id="manual-3",
            ),
        )
        with pytest.raises(DeferredAI):
            pipeline.extract_job(session, job, DeferredClient())
        assert job.quality_status == "pending"
        assert job.extraction_attempts == 0
        assert job.extracted_at is None


def test_pending_extraction_processes_newest_source_first(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None
    calls: list[str] = []
    body = _job_body()

    class RecordingAI:
        def extract_job(self, prompt: str):
            calls.append(prompt)
            from app.ai import Generated

            return Generated(value=_extraction(text=body), model="example/free")

    factory = _factory(tmp_path)
    with factory() as session:
        old = upsert_job(
            session,
            JobInput(
                title="Old pending",
                company="Needs review",
                description=f"OLD Data Engineer Example Health Responsibilities {body}",
                source="manual",
                external_id="old",
            ),
        )
        new = upsert_job(
            session,
            JobInput(
                title="New pending",
                company="Needs review",
                description=f"NEW Data Engineer Example Health Responsibilities {body}",
                source="manual",
                external_id="new",
            ),
        )
        old_source = session.scalar(select(JobSource).where(JobSource.job_id == old.id))
        new_source = session.scalar(select(JobSource).where(JobSource.job_id == new.id))
        assert old_source is not None
        assert new_source is not None
        old_source.discovered_at = datetime(2026, 7, 1, tzinfo=UTC)
        new_source.discovered_at = datetime(2026, 7, 19, tzinfo=UTC)
        session.commit()

        processed = pipeline.extract_pending_jobs(
            session,
            RecordingAI(),
            max_jobs=1,
        )

        assert processed == 1
        assert "NEW Data Engineer" in calls[0]
        assert new.quality_status == "verified"
        assert old.quality_status == "pending"


def test_brave_results_are_untrusted_pending_discoveries(tmp_path: Path) -> None:
    pipeline = _module()
    assert pipeline is not None

    class Search:
        def search(self, _query: str) -> list[SearchResult]:
            return [
                SearchResult(
                    title="Discover 2,000 Data Analyst Jobs | Indeed",
                    url="https://example.com/data-jobs",
                    snippet="Search thousands of jobs.",
                )
            ]

    with _factory(tmp_path)() as session:
        assert pipeline.backfill_jobs(session, Search(), query="data analyst") == 1
        job = session.scalar(select(Job))
        assert job is not None
        assert job.quality_status == "pending"
        assert job.description == "Search thousands of jobs."

from __future__ import annotations

import base64
from pathlib import Path

from sqlalchemy import select

from app.db import create_schema, make_engine, make_session_factory
from app.models import IngestMessage, Job, JobSource


def _module():
    try:
        from app import ingest

        return ingest
    except ImportError:
        return None


def _session(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    create_schema(engine)
    return make_session_factory(engine)()


WORKDAY_TEXT = """
Software Developer
locations
Vancouver, BC, Canada
job requisition id
JR25262

Job Summary
The Software Developer designs, tests, and maintains research data platforms.

Work Performed
Build reliable full-stack applications and relational database services.
"""


def test_parses_workday_style_text_deterministically() -> None:
    ingest = _module()
    assert ingest is not None
    parsed = ingest.parse_job_text(WORKDAY_TEXT, company="Example University")
    assert parsed.title == "Software Developer"
    assert parsed.company == "Example University"
    assert parsed.location == "Vancouver, BC, Canada"
    assert parsed.requisition_id == "JR25262"
    assert "research data platforms" in parsed.description


def test_upsert_is_idempotent_and_preserves_source_lineage(tmp_path: Path) -> None:
    ingest = _module()
    assert ingest is not None
    with _session(tmp_path) as session:
        first = ingest.upsert_job(
            session,
            ingest.JobInput(
                title="Software Developer",
                company="Example University",
                location="Vancouver",
                description="Build reliable systems.",
                requisition_id="JR25262",
                url="https://careers.example.edu/job/25262?utm_source=linkedin",
                source="gmail",
                external_id="message-1",
            ),
        )
        first.status = "applied"
        first.notes = "Applied on Friday"
        session.commit()
        second = ingest.upsert_job(
            session,
            ingest.JobInput(
                title="Software Developer",
                company="Example University",
                location="Vancouver, BC",
                description="Updated source description.",
                requisition_id="JR25262",
                url="https://careers.example.edu/job/25262",
                source="manual",
                external_id="paste-1",
            ),
        )
        assert second.id == first.id
        assert second.status == "applied"
        assert second.notes == "Applied on Friday"
        assert len(session.scalars(select(Job)).all()) == 1
        assert {row.source for row in session.scalars(select(JobSource)).all()} == {
            "gmail",
            "manual",
        }


def test_decodes_multipart_gmail_alert_and_filters_job_links() -> None:
    ingest = _module()
    assert ingest is not None
    mime = (
        "Subject: =?UTF-8?Q?3_new_data_jobs?=\r\n"
        "Date: Sat, 18 Jul 2026 09:00:00 -0700\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="x"\r\n\r\n'
        "--x\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "Software Developer https://www.linkedin.com/jobs/view/123/?trk=email\r\n"
        "--x\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        '<a href="https://www.linkedin.com/jobs/view/123/?utm_source=email">Job</a>'
        '<a href="https://www.linkedin.com/feed/">Feed</a>\r\n'
        "--x--\r\n"
    )
    raw = base64.urlsafe_b64encode(mime.encode()).decode().rstrip("=")
    alert = ingest.parse_gmail_raw("gmail-42", raw)
    assert alert.external_id == "gmail-42"
    assert alert.subject == "3 new data jobs"
    assert alert.links == ["https://linkedin.com/jobs/view/123"]
    assert "Software Developer" in alert.text


def test_recording_same_gmail_message_twice_is_safe(tmp_path: Path) -> None:
    ingest = _module()
    assert ingest is not None
    with _session(tmp_path) as session:
        assert ingest.record_ingest_message(session, "gmail", "same-id", "Alert")
        assert not ingest.record_ingest_message(session, "gmail", "same-id", "Alert")
        assert len(session.scalars(select(IngestMessage)).all()) == 1

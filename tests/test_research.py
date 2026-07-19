from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db import create_schema, make_engine, make_session_factory
from app.ingest import JobInput, upsert_job
from app.models import Contact, ContactEmail, ContactEvidence, JobContact


def _module():
    try:
        from app import research

        return research
    except ImportError:
        return None


def _session(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'research.db'}")
    create_schema(engine)
    return make_session_factory(engine)()


def test_ranks_role_relevance_and_caps_recommendations() -> None:
    research = _module()
    assert research is not None
    candidates = [
        research.ContactCandidate("A", "Manager, Research Data Services", "Example U", "u1"),
        research.ContactCandidate("B", "Software Developer", "Example U", "u2"),
        research.ContactCandidate("C", "Talent Acquisition Partner", "Example U", "u3"),
        research.ContactCandidate("D", "Professor of History", "Example U", "u4"),
    ]
    ranked = research.rank_contacts(
        candidates,
        company="Example U",
        job_title="Junior Data Coordinator",
        department="Research Data Services",
    )
    assert len(ranked) == 3
    assert ranked[0].name == "A"
    assert {candidate.name for candidate in ranked} == {"A", "B", "C"}


def test_contact_is_reused_across_jobs_and_job_cap_is_enforced(tmp_path: Path) -> None:
    research = _module()
    assert research is not None
    with _session(tmp_path) as session:
        jobs = [
            upsert_job(
                session,
                JobInput(
                    title=f"Data Role {number}",
                    company="Example U",
                    description="Data work",
                    source="manual",
                    external_id=f"job-{number}",
                ),
            )
            for number in (1, 2)
        ]
        shared = research.ContactCandidate(
            "Ada Lovelace",
            "Manager, Research Data Services",
            "Example U",
            "https://example.edu/people/ada",
        )
        first_ids = research.save_recommendations(
            session,
            jobs[0],
            [shared]
            + [
                research.ContactCandidate(
                    f"Person {number}", "Recruiter", "Example U", f"https://example.edu/p/{number}"
                )
                for number in range(5)
            ],
        )
        second_ids = research.save_recommendations(session, jobs[1], [shared])
        assert len(first_ids) == 3
        assert second_ids == [first_ids[0]]
        assert len(session.scalars(select(Contact)).all()) == 3
        assert (
            len(session.scalars(select(JobContact).where(JobContact.job_id == jobs[0].id)).all())
            == 3
        )


def test_evidence_is_short_deduplicated_and_keeps_provenance(tmp_path: Path) -> None:
    research = _module()
    assert research is not None
    with _session(tmp_path) as session:
        contact = Contact(name="Ada Lovelace", title="Manager", company="Example U")
        session.add(contact)
        session.commit()
        first = research.save_evidence(
            session,
            contact,
            title="Public research profile",
            source_url="https://example.edu/ada",
            excerpt="A" * 900,
            kind="official",
        )
        second = research.save_evidence(
            session,
            contact,
            title="Public research profile",
            source_url="https://example.edu/ada",
            excerpt="A" * 900,
            kind="official",
        )
        assert second.id == first.id
        assert len(first.excerpt) == 500
        assert first.source_url == "https://example.edu/ada"
        assert len(session.scalars(select(ContactEvidence)).all()) == 1


def test_builds_bounded_public_search_queries() -> None:
    research = _module()
    assert research is not None
    queries = research.contact_queries(
        company="Example University",
        department="Research Data Services",
        job_title="Junior Data Coordinator",
    )
    assert 1 <= len(queries) <= 3
    assert all(len(query) <= 180 for query in queries)
    assert all("Example University" in query for query in queries)


def test_public_email_keeps_source_and_confidence_label(tmp_path: Path) -> None:
    research = _module()
    assert research is not None
    with _session(tmp_path) as session:
        contact = Contact(name="Ada Lovelace", title="Manager", company="Example U")
        session.add(contact)
        session.commit()
        rows = research.save_public_emails(
            session,
            contact,
            text="Questions can be sent to Ada.Lovelace@example.edu.",
            source_url="https://research.example.edu/team/ada",
        )
        assert len(rows) == 1
        assert rows[0].email == "ada.lovelace@example.edu"
        assert rows[0].confidence == "verified_public_official"
        assert rows[0].source_url == "https://research.example.edu/team/ada"
        assert len(session.scalars(select(ContactEmail)).all()) == 1

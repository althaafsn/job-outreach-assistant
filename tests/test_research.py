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
            excerpt=(
                "Ada Lovelace leads research data services at Example University. "
                "Her team builds secure data platforms for public-interest research, "
                "documents reproducible workflows, and trains researchers in careful "
                "data stewardship. "
            )
            * 5,
            kind="official",
        )
        second = research.save_evidence(
            session,
            contact,
            title="Public research profile",
            source_url="https://example.edu/ada",
            excerpt=(
                "Ada Lovelace leads research data services at Example University. "
                "Her team builds secure data platforms for public-interest research, "
                "documents reproducible workflows, and trains researchers in careful "
                "data stewardship. "
            )
            * 5,
            kind="official",
        )
        assert first is not None
        assert second is not None
        assert second.id == first.id
        assert len(first.excerpt) == 500
        assert first.source_url == "https://example.edu/ada"
        assert len(session.scalars(select(ContactEvidence)).all()) == 1


def test_evidence_rejects_linkedin_shells_and_unrelated_pages(tmp_path: Path) -> None:
    research = _module()
    assert research is not None
    with _session(tmp_path) as session:
        contact = Contact(name="Ada Lovelace", title="Manager", company="Example U")
        session.add(contact)
        session.commit()

        linkedin = research.save_evidence(
            session,
            contact,
            title="Ada Lovelace | LinkedIn",
            source_url="https://ca.linkedin.com/in/ada",
            excerpt=(
                "Ada Lovelace. Agree and join LinkedIn. Sign in to see this profile. "
                "User Agreement Privacy Policy Cookie Policy and community guidelines."
            ),
        )
        title_only = research.save_evidence(
            session,
            contact,
            title="Ada Lovelace - Manager",
            source_url="https://directory.example.edu/ada",
            excerpt="Ada Lovelace is a manager at Example University.",
        )
        unrelated = research.save_evidence(
            session,
            contact,
            title="Research data services",
            source_url="https://example.edu/research-data",
            excerpt=(
                "Our university provides secure repositories, consultation, training, "
                "and reproducible research support for faculty and graduate students. "
                "The service follows institutional privacy and security requirements."
            ),
        )

        assert linkedin is None
        assert title_only is None
        assert unrelated is None
        assert session.scalars(select(ContactEvidence)).all() == []


def test_evidence_accepts_substantive_named_public_work(tmp_path: Path) -> None:
    research = _module()
    assert research is not None
    with _session(tmp_path) as session:
        contact = Contact(name="Ada Lovelace", title="Manager", company="Example U")
        session.add(contact)
        session.commit()
        evidence = research.save_evidence(
            session,
            contact,
            title="Ada Lovelace leads the Open Records Project",
            source_url="https://example.edu/news/open-records",
            excerpt=(
                "Ada Lovelace leads the Open Records Project at Example University. "
                "The project standardizes public research records and publishes reusable "
                "documentation so community partners can audit and extend the datasets. "
                "Lovelace described how the team balances access, privacy, and validation."
            ),
        )
        assert evidence is not None
        assert evidence.title == "Ada Lovelace leads the Open Records Project"


def test_evidence_can_require_the_selected_organization() -> None:
    research = _module()
    assert research is not None
    unrelated = (
        "Jenny Mackay is a researcher at Nottingham Trent University. Her public "
        "work examines social policy, program evaluation, and community services. "
        "She publishes reports and collaborates with research partners in England."
    )
    official = (
        "Jenny Mackay is Human Resources Manager at the University of British "
        "Columbia. She supports recruitment, employee relations, and organizational "
        "planning for teams across UBC."
    )
    assert (
        research.evidence_rejection_reason(
            unrelated,
            person_name="Jenny Mackay",
            source_url="https://ntu.ac.uk/staff/jenny-mackay",
            organization="University of British Columbia",
        )
        == "Page does not confirm the selected organization"
    )
    assert (
        research.evidence_rejection_reason(
            official,
            person_name="Jenny Mackay",
            source_url="https://hr.ubc.ca/jenny-mackay",
            organization="University of British Columbia",
        )
        is None
    )


def test_contact_choice_requires_a_full_name_and_job_context() -> None:
    research = _module()
    assert research is not None
    context = ["Research Data Services", "Faculty360"]
    assert (
        research.contact_choice_rejection_reason(
            name="Kate L.",
            title="Program Manager",
            source_text="Kate L. is a Program Manager at Example University.",
            focus_terms=context,
        )
        == "Selected person does not have a sufficiently specific public name"
    )
    assert (
        research.contact_choice_rejection_reason(
            name="Angela Lam",
            title="Senior Manager",
            source_text="Angela Lam is a Senior Manager in an unrelated arts unit.",
            focus_terms=context,
        )
        == "Selected role is not tied to the job's unit or platform"
    )
    assert (
        research.contact_choice_rejection_reason(
            name="Ada Lovelace",
            title="Manager of Research Data Services",
            source_text=(
                "Ada Lovelace is Manager of Research Data Services and supports Faculty360."
            ),
            focus_terms=context,
        )
        is None
    )


def test_grounded_contact_title_falls_back_to_a_public_role_phrase() -> None:
    research = _module()
    assert research is not None
    source = (
        "Ashley McKerrow. Ashley is an experienced data management professional "
        "with over twelve years of experience supporting health research."
    )
    assert (
        research.grounded_contact_title(
            name="Ashley McKerrow",
            proposed="Manager, Research Data Services",
            source_text=source,
        )
        == "Experienced data management professional"
    )
    assert (
        research.grounded_contact_title(
            name="Ada Lovelace",
            proposed="Manager, Research Data Services",
            source_text="Ada Lovelace - Manager, Research Data Services at Example University.",
        )
        == "Manager, Research Data Services"
    )
    assert (
        research.grounded_contact_title(
            name="Ada Lovelace",
            proposed="Vice President of AI",
            source_text="Ada Lovelace manages research data services.",
        )
        is None
    )


def test_relevant_excerpt_keeps_context_buried_in_a_long_team_page() -> None:
    research = _module()
    assert research is not None
    text = ("General organization information and navigation. " * 120) + (
        "Ashley McKerrow is an experienced data management professional who leads "
        "full Research Data Services, including training, platform validation, and "
        "documentation for health research teams."
    )
    excerpt = research.relevant_excerpt(text, ["Research Data Services"])
    assert "Ashley McKerrow" in excerpt
    assert "platform validation" in excerpt
    assert len(excerpt) <= 3_000


def test_builds_bounded_public_search_queries() -> None:
    research = _module()
    assert research is not None
    queries = research.contact_queries(
        company="University of British Columbia",
        department="",
        job_title="Junior Data Coordinator",
        description=(
            "The role supports Faculty360 for the Faculty of Medicine. "
            "Reporting to the Manager, Research Data Services, the coordinator "
            "maintains research platforms."
        ),
    )
    assert len(queries) == 3
    assert all(len(query) <= 180 for query in queries)
    assert any("UBC" in query for query in queries)
    assert any("Faculty360" in query and "Research Data Services" in query for query in queries)
    assert sum("site:linkedin.com/in" in query for query in queries) == 1
    assert any("-site:linkedin.com" in query for query in queries)


def test_builds_person_specific_research_queries_without_linkedin() -> None:
    research = _module()
    assert research is not None
    queries = research.person_research_queries(
        name="Ada Lovelace",
        company="Example University",
    )
    assert len(queries) == 3
    assert all('"Ada Lovelace"' in query for query in queries)
    assert all("-site:linkedin.com" in query for query in queries)
    assert any("publication" in query for query in queries)
    assert any("interview" in query for query in queries)


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

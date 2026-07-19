from __future__ import annotations

import importlib


def _domain():
    try:
        return importlib.import_module("app.domain")
    except ModuleNotFoundError:
        return None


def test_normalizes_human_text_without_destroying_words() -> None:
    domain = _domain()
    assert domain is not None
    assert domain.normalize_text("  Junior—Data   Developer  ") == "junior data developer"


def test_canonical_url_removes_tracking_and_normalizes_host() -> None:
    domain = _domain()
    assert domain is not None
    url = "HTTPS://WWW.Example.COM/jobs/123/?utm_source=linkedin&gh_jid=123#apply"
    assert domain.canonical_url(url) == "https://example.com/jobs/123?gh_jid=123"


def test_job_identity_prefers_source_id_then_requisition_then_url() -> None:
    domain = _domain()
    assert domain is not None
    assert domain.job_keys(
        source="linkedin",
        external_id="987",
        requisition_id="JR42",
        company="Example Health",
        title="Data Developer",
        location="Vancouver, BC",
        url="https://example.org/job/42",
        description="Build reliable pipelines.",
    )[:3] == [
        "source:linkedin:987",
        "req:example health:jr42",
        "url:https://example.org/job/42",
    ]


def test_job_identity_has_content_fallback() -> None:
    domain = _domain()
    assert domain is not None
    keys = domain.job_keys(
        source="manual",
        external_id=None,
        requisition_id=None,
        company=" Example  Health ",
        title="DATA DEVELOPER",
        location="Vancouver",
        url=None,
        description="Build reliable pipelines.",
    )
    assert len(keys) == 1
    assert keys[0].startswith("content:example health:data developer:vancouver:")


def test_contact_keys_use_email_profile_and_name_company() -> None:
    domain = _domain()
    assert domain is not None
    assert domain.contact_keys(
        name=" Dr. Ada  Lovelace ",
        company="Example Health",
        email="ADA@EXAMPLE.ORG",
        profile_url="https://example.org/people/ada/?utm_source=x",
    ) == [
        "email:ada@example.org",
        "profile:https://example.org/people/ada",
        "person:dr ada lovelace:example health",
    ]

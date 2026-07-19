from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.db import create_schema, make_engine


def _client(tmp_path: Path) -> TestClient:
    try:
        from app.api import create_app
    except ImportError:
        create_app = None
    assert create_app is not None
    engine = make_engine(
        f"sqlite:///{tmp_path / 'api.db'}",
    )
    create_schema(engine)
    return TestClient(create_app(engine))


def test_import_list_update_and_dashboard_flow(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        imported = client.post(
            "/api/jobs/import",
            json={
                "text": (
                    "Junior Data Coordinator\nlocations\nVancouver, BC\n"
                    "job requisition id\nJR25237\n\nJob Summary\n"
                    "Support secure research data platforms."
                ),
                "company": "Example University",
                "url": "https://careers.example.edu/jobs/25237?utm_source=test",
            },
        )
        assert imported.status_code == 201
        job_id = imported.json()["id"]
        assert client.get("/api/jobs").json()["items"][0]["requisition_id"] == "JR25237"

        updated = client.patch(
            f"/api/jobs/{job_id}",
            json={"status": "applied", "notes": "Applied through Workday"},
        )
        assert updated.json()["status"] == "applied"
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["jobs"]["total"] == 1
        assert dashboard["jobs"]["applied"] == 1


def test_add_contact_evidence_angle_draft_and_manual_outreach_event(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        job_id = client.post(
            "/api/jobs/import",
            json={"text": "Data Developer\nJob Summary\nBuild data tools.", "company": "Example U"},
        ).json()["id"]
        contact = client.post(
            f"/api/jobs/{job_id}/contacts",
            json={
                "name": "Ada Lovelace",
                "title": "Research Data Manager",
                "company": "Example U",
                "profile_url": "https://example.edu/ada",
            },
        )
        assert contact.status_code == 201
        contact_id = contact.json()["id"]
        evidence = client.post(
            f"/api/contacts/{contact_id}/evidence",
            json={
                "title": "Public program page",
                "source_url": "https://example.edu/program",
                "excerpt": "Ada led the public launch of a research data training program.",
                "kind": "official",
            },
        )
        assert evidence.status_code == 201
        angle = client.post(
            f"/api/jobs/{job_id}/contacts/{contact_id}/angles",
            json={
                "angle": "Ask about launching the public training program.",
                "question": "What did you learn from its first users?",
                "evidence_ids": [evidence.json()["id"]],
            },
        )
        assert angle.status_code == 201
        draft = client.post(
            f"/api/jobs/{job_id}/contacts/{contact_id}/drafts",
            json={
                "kind": "connection_note",
                "body": "Hi Ada, I was interested in your research data training launch. "
                "I would value hearing what you learned from its first users.",
                "subjects": [],
                "angle_id": angle.json()["id"],
            },
        )
        assert draft.status_code == 201
        event = client.post(
            "/api/outreach-events",
            json={
                "job_id": job_id,
                "contact_id": contact_id,
                "draft_id": draft.json()["id"],
                "type": "connection_sent",
            },
        )
        assert event.status_code == 201
        detail = client.get(f"/api/jobs/{job_id}").json()
        assert detail["contacts"][0]["evidence"][0]["id"] == evidence.json()["id"]
        assert detail["contacts"][0]["angles"][0]["id"] == angle.json()["id"]


def test_validation_and_missing_resources_are_clear(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.post("/api/jobs/import", json={"text": ""}).status_code == 422
        assert client.get("/api/jobs/9999").status_code == 404
        health = client.get("/api/health").json()
        assert health == {"status": "ok"}


def test_optional_ai_and_search_actions_explain_missing_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("BRAVE_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with _client(tmp_path) as client:
            job_id = client.post(
                "/api/jobs/import",
                json={"text": "Data Analyst\nJob Summary\nAnalyze data.", "company": "Example U"},
            ).json()["id"]
            assert client.post(f"/api/jobs/{job_id}/research").status_code == 409
            settings = client.get("/api/settings").json()
            assert settings["openrouter_configured"] is False
            assert settings["brave_search_configured"] is False
    finally:
        get_settings.cache_clear()


def test_angle_generation_accepts_a_user_selected_perspective(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        operation = client.get("/openapi.json").json()["paths"][
            "/api/jobs/{job_id}/angles/generate"
        ]["post"]
        assert "requestBody" in operation


def test_private_data_deletion_requires_explicit_confirmation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        job_id = client.post(
            "/api/jobs/import",
            json={"text": "Data Analyst\nJob Summary\nAnalyze data.", "company": "Example U"},
        ).json()["id"]
        contact_id = client.post(
            f"/api/jobs/{job_id}/contacts",
            json={
                "name": "Ada Lovelace",
                "title": "Research Data Manager",
                "company": "Example U",
            },
        ).json()["id"]
        evidence_id = client.post(
            f"/api/contacts/{contact_id}/evidence",
            json={
                "title": "Official profile",
                "source_url": "https://example.edu/ada",
                "excerpt": "Ada leads research data services.",
                "kind": "official",
            },
        ).json()["id"]
        angle_id = client.post(
            f"/api/jobs/{job_id}/contacts/{contact_id}/angles",
            json={
                "angle": "Ask about research data services.",
                "question": "What has changed most in the work?",
                "evidence_ids": [evidence_id],
            },
        ).json()["id"]
        draft_id = client.post(
            f"/api/jobs/{job_id}/contacts/{contact_id}/drafts",
            json={
                "kind": "connection_note",
                "body": "Hi Ada, I would value your perspective on research data services.",
                "subjects": [],
                "angle_id": angle_id,
            },
        ).json()["id"]
        client.post(
            "/api/outreach-events",
            json={
                "job_id": job_id,
                "contact_id": contact_id,
                "draft_id": draft_id,
                "type": "connection_sent",
            },
        )
        assert client.delete("/api/data").status_code == 422
        deleted = client.delete("/api/data?confirm=DELETE")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"]["jobs"] == 1
        assert client.get("/api/jobs").json()["items"] == []

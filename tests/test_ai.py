from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.db import create_schema, make_engine, make_session_factory


def _module():
    try:
        from app import ai

        return ai
    except ImportError:
        return None


def _session(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'ai.db'}")
    create_schema(engine)
    return make_session_factory(engine)()


def test_angle_output_rejects_unknown_evidence_ids() -> None:
    ai = _module()
    assert ai is not None
    output = ai.AngleOutput.model_validate(
        {
            "angles": [
                {
                    "angle": "Ask about the public research program.",
                    "question": "What changed your view while leading it?",
                    "evidence_ids": [99],
                }
            ]
        }
    )
    with pytest.raises(ai.UngroundedOutput):
        ai.require_known_evidence(output, {1, 2})


def test_angle_output_accepts_common_free_model_field_aliases() -> None:
    ai = _module()
    assert ai is not None
    output = ai.AngleOutput.model_validate(
        {
            "conversation_angles": [
                {
                    "topic": "Ask about the public research program.",
                    "question": "What changed your view while leading it?",
                    "cited_evidence": [1],
                }
            ]
        }
    )
    assert output.angles[0].angle.startswith("Ask about")
    assert output.angles[0].evidence_ids == [1]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "connection_note", "subjects": [], "body": "x" * 281},
        {"kind": "post_connection", "subjects": [], "body": "Too short."},
        {"kind": "email", "subjects": ["Only one"], "body": "word " * 100},
        {"kind": "email", "subjects": ["One", "Two"], "body": "word " * 141},
    ],
)
def test_draft_schema_enforces_channel_length_contract(payload: dict) -> None:
    ai = _module()
    assert ai is not None
    with pytest.raises(ValidationError):
        ai.DraftOutput.model_validate(payload)


def test_prompt_contains_only_whitelisted_profile_and_public_evidence() -> None:
    ai = _module()
    assert ai is not None
    prompt = ai.build_angle_prompt(
        job={"title": "Data Developer", "company": "Example U", "notes": "PRIVATE"},
        contact={
            "name": "Ada",
            "title": "Research Manager",
            "email": "ada@example.edu",
        },
        evidence=[
            {
                "id": 7,
                "title": "Public paper",
                "excerpt": "A public research finding.",
                "source_url": "https://example.edu/paper",
                "gmail_header": "SECRET",
            }
        ],
        profile_summary="Computer Engineering graduate interested in data systems.",
    )
    assert "Data Developer" in prompt
    assert "A public research finding" in prompt
    assert "PRIVATE" not in prompt
    assert "ada@example.edu" not in prompt
    assert "SECRET" not in prompt


def test_openrouter_repairs_invalid_json_once_and_records_actual_model(tmp_path: Path) -> None:
    ai = _module()
    assert ai is not None
    calls = 0
    valid = {
        "angles": [
            {
                "angle": "Discuss the public data platform launch.",
                "question": "What was the hardest adoption decision?",
                "evidence_ids": [7],
            }
        ]
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "not json" if calls == 1 else json.dumps(valid)
        return httpx.Response(
            200,
            json={
                "model": "meta-llama/free-model",
                "choices": [{"message": {"content": content}}],
            },
        )

    with _session(tmp_path) as session:
        client = ai.OpenRouterClient(
            api_key="test",
            session=session,
            transport=httpx.MockTransport(handler),
            daily_limit=2,
        )
        result = client.generate_angles("prompt", allowed_evidence_ids={7})
        assert calls == 2
        assert result.model == "meta-llama/free-model"
        assert result.value.angles[0].evidence_ids == [7]


def test_openrouter_defers_on_429_and_when_daily_quota_is_exhausted(
    tmp_path: Path,
) -> None:
    ai = _module()
    assert ai is not None
    transport = httpx.MockTransport(lambda _request: httpx.Response(429))
    with _session(tmp_path) as session:
        client = ai.OpenRouterClient(
            api_key="test", session=session, transport=transport, daily_limit=2
        )
        with pytest.raises(ai.DeferredAI):
            client.generate_angles("prompt", allowed_evidence_ids={1})


def test_offline_contract_eval_validates_schema_and_grounding() -> None:
    ai = _module()
    assert ai is not None
    rows = [
        {
            "name": "angle",
            "allowed_evidence_ids": [1],
            "output": {
                "angles": [
                    {
                        "angle": "Discuss the public research program.",
                        "question": "What did its first users change?",
                        "evidence_ids": [1],
                    }
                ]
            },
        },
        {
            "name": "draft",
            "output": {
                "kind": "connection_note",
                "subjects": [],
                "body": "Hi Ada, your public data-training work caught my attention.",
            },
        },
    ]
    assert ai.evaluate_contracts(rows) == {"passed": 2, "failed": []}

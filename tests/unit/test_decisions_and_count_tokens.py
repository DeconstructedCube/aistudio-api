"""Unit tests for Decisions API (/v1beta/decisions), CountTokens, and ListQuotaModels."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google import genai
from google.genai import types

from aistudio_api.api.app import app
from aistudio_api.api.dependencies import get_client
from aistudio_api.application.account_rotator import AccountRotator
from aistudio_api.config import settings
from aistudio_api.domain.errors import AuthError
from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.infrastructure.gateway.model_discovery import ModelDiscoveryService
from aistudio_api.infrastructure.gateway.wire_codec import (
    encode_count_tokens_body,
    encode_decision_request_body,
    extract_decision_digest_contents,
)
from aistudio_api.infrastructure.gateway.wire_parser import (
    parse_count_tokens_response,
    parse_decision_response,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def test_decision_wire_codec_and_digest_isolation() -> None:
    """Verify CreateDecision JSPB encoding and WAA digest excludes question instructions."""
    digest_contents = extract_decision_digest_contents(
        [
            {"type": "text", "text": "Cracked blue mug"},
            {"type": "image", "data": "AAAA", "mime_type": "image/png"},
        ]
    )
    assert len(digest_contents) == 1
    assert digest_contents[0].parts[0].text == "Cracked blue mug"
    assert digest_contents[0].parts[1].inline_data == ("image/png", "AAAA")

    raw_body = encode_decision_request_body(
        model="gemini-decision-1",
        raw_input=[
            {"type": "text", "text": "Cracked blue mug"},
            {"type": "image", "data": "AAAA", "mime_type": "image/png"},
        ],
        questions={
            "is_damaged": {
                "type": "noul",
                "instructions": "Did it arrive damaged?",
                "criteria": {
                    "true_description": "Broken",
                    "false_description": "Intact",
                },
            },
            "queue": {
                "type": "choice",
                "instructions": "Which queue?",
                "criteria": {
                    "shipping": "Delivery",
                    "billing": "Refund",
                },
            },
            "severity": {
                "type": "score",
                "instructions": "How severe?",
                "criteria": ["Low", "Medium", "High"],
            },
        },
        temperature=0.0,
        snapshot="!proof_token",
    )
    parsed = json.loads(raw_body)
    assert parsed[0] == "models/gemini-decision-1"
    # Input: TextContent at [0], ImageContent(png=1) at [1]
    assert parsed[1] == [[["Cracked blue mug", None]], [None, [1, "AAAA"]]]
    # Questions map
    q_map = dict(parsed[2])
    assert q_map["is_damaged"] == [
        [[["Did it arrive damaged?", None]]],
        [["Broken", "Intact"]],
    ]
    # Choice options sorted alphabetically by UTF-8 key
    assert q_map["queue"] == [
        [[["Which queue?", None]]],
        None,
        [[["billing", "Refund"], ["shipping", "Delivery"]]],
    ]
    assert q_map["severity"] == [
        [[["How severe?", None]]],
        None,
        None,
        [["Low", "Medium", "High"]],
    ]
    # temperature=0.0 omitted (upstream rejects non-positive), snapshot at index 4
    assert parsed[3] is None
    assert parsed[4] == "!proof_token"


def test_parse_decision_response_all_question_types() -> None:
    """Verify JSPB CreateDecisionResponse parsing for noul, choice, score, and usage."""
    raw = json.dumps(
        [
            [
                [
                    "churn_risk",
                    [
                        0.99,
                        1,
                        None,
                        None,
                        None,
                        [
                            1.98,
                            2,
                            0.01,
                            [[0, 0.0], [1, 0.02], [2, 0.98]],
                            [[0, "Low"], [1, "Mid"], [2, "High"]],
                        ],
                    ],
                ],
                [
                    "is_defect",
                    [0.95, 1, None, [1, 0.95, 0.95, 0.05, -1.3, -7.4]],
                ],
                [
                    "next_action",
                    [
                        0.6,
                        1,
                        None,
                        None,
                        ["escalate", [["refund", 0.4], ["escalate", 0.6]]],
                    ],
                ],
            ],
            [254, [[1, 200], [2, 54]], None, None, None, None, None, None, None, 254],
        ]
    )
    decoded = parse_decision_response(raw)
    answers = decoded["answers"]
    assert isinstance(answers, dict)
    assert answers["is_defect"] == {"type": "noul", "noul": 0.95, "confidence": 0.95}
    assert answers["next_action"] == {
        "type": "choice",
        "choice": "escalate",
        "probabilities": {"refund": 0.4, "escalate": 0.6},
        "confidence": 0.6,
    }
    assert answers["churn_risk"] == {
        "type": "score",
        "score": 1.98,
        "legend": {"0": "Low", "1": "Mid", "2": "High"},
        "probabilities": {"0": 0.0, "1": 0.02, "2": 0.98},
        "confidence": 0.99,
    }
    assert decoded["usage"] == {
        "total_input_tokens": 254,
        "input_tokens_by_modality": [
            {"modality": "text", "tokens": 200},
            {"modality": "image", "tokens": 54},
        ],
        "total_output_tokens": 0,
        "total_tokens": 254,
    }


@pytest.mark.asyncio
async def test_decisions_endpoint_and_403_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test POST /v1beta/decisions REST contract and 403 self-healing retry."""
    monkeypatch.setattr(settings, "api_keys", frozenset())
    mock_client = MagicMock()
    mock_client.prepare_decision_request = AsyncMock(
        return_value=MagicMock(
            url="https://example.test", headers={}, body="[]", ctx=None
        )
    )
    mock_client.create_decision = AsyncMock(
        side_effect=[
            AuthError("The caller does not have permission"),
            {
                "answers": {
                    "is_damaged": {"type": "noul", "noul": 0.98, "confidence": 0.98}
                },
                "usage": {
                    "total_input_tokens": 94,
                    "input_tokens_by_modality": [{"modality": "text", "tokens": 94}],
                    "total_output_tokens": 0,
                    "total_tokens": 94,
                },
            },
        ]
    )
    app.dependency_overrides[get_client] = lambda: mock_client
    try:
        async with _build_client() as client:
            resp = await client.post(
                "/v1beta/decisions",
                json={
                    "model": "gemini-decision-1",
                    "input": "Mug arrived cracked.",
                    "questions": {
                        "is_damaged": {
                            "type": "noul",
                            "instructions": "Did it arrive damaged?",
                        }
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["answers"]["is_damaged"]["noul"] == 0.98
            assert data["usage"]["total_input_tokens"] == 94
            assert data["usage"]["total_output_tokens"] == 0
            assert mock_client.create_decision.await_count == 2
    finally:
        app.dependency_overrides.clear()


def test_count_tokens_wire_codec_and_parser() -> None:
    body = encode_count_tokens_body(
        model="gemini-3.8-flash",
        contents=[AistudioContent(role="user", parts=[AistudioPart(text="hello")])],
    )
    assert json.loads(body) == [
        "models/gemini-3.8-flash",
        [[[[None, "hello"]], "user"]],
    ]
    parsed = parse_count_tokens_response("[12,[],[[[2],1]],4,null,[[1,12]]]")
    assert parsed == {
        "totalTokens": 12,
        "cachedContentTokenCount": 4,
        "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 12}],
    }


@pytest.mark.asyncio
async def test_count_tokens_sdk_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify google-genai SDK models.count_tokens works against /v1beta/...:countTokens."""
    monkeypatch.setattr(settings, "api_keys", frozenset())
    mock_client = MagicMock()
    mock_client.session = MagicMock(active_context=None)
    mock_client.count_tokens = AsyncMock(
        return_value={
            "totalTokens": 7,
            "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 7}],
        }
    )
    app.dependency_overrides[get_client] = lambda: mock_client
    custom_client: httpx.AsyncClient | None = None
    try:
        sdk_client = genai.Client(
            api_key="test-key",
            http_options=types.HttpOptions(base_url="http://testserver"),
        )
        async with sdk_client.aio as aio_client:
            custom_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            )
            aio_client._api_client._async_httpx_client = custom_client
            result = await aio_client.models.count_tokens(
                model="gemini-3.8-flash",
                contents="Count these tokens",
            )
            assert result.total_tokens == 7
            mock_client.count_tokens.assert_awaited_once()
    finally:
        if custom_client is not None:
            await custom_client.aclose()
        app.dependency_overrides.clear()


def test_list_models_includes_decision_and_quota_family_cooldowns(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify gemini-decision-1 is parsed from ListModels and ListQuotaModels shares 429 cooldowns."""
    svc = ModelDiscoveryService()
    dec_row: list[object] = [None] * 85
    dec_row[0] = "models/gemini-decision-1"
    dec_row[2] = "gemini-decision-1"
    dec_row[3] = "Gemini Decision 1"
    dec_row[5] = 262144
    dec_row[6] = 1
    dec_row[7] = ["createDecision"]

    parsed_models = svc._parse_raw_models([[dec_row]])
    assert len(parsed_models) == 1
    assert parsed_models[0]["id"] == "gemini-decision-1"
    assert parsed_models[0]["category"] == "decision"
    assert parsed_models[0]["supportedGenerationMethods"] == ["createDecision"]

    svc._parse_quota_models(
        [
            [
                [
                    "gemini-3.8-flash",
                    None,
                    4,
                    "gemini-3",
                    [
                        ["models/gemini-3.8-flash", [1], [8]],
                        ["models/gemini-flash-latest", [1], [8]],
                    ],
                    "gemini-3.8-flash",
                    "Gemini 3.8 Flash",
                ]
            ]
        ]
    )
    assert svc.resolve_quota_family("models/gemini-flash-latest") == "gemini-3.8-flash"
    assert svc.get_quota_family_siblings("gemini-3.8-flash") == (
        "gemini-3.8-flash",
        "gemini-flash-latest",
    )

    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.model_discovery.model_discovery", svc
    )
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    acc = store.save_account(
        name="acc1", email="u1@test.com", storage_state={"cookies": []}
    )
    rotator = AccountRotator(store)
    rotator.record_rate_limited(acc.id, model="gemini-3.8-flash")

    assert rotator.is_account_available(acc.id, model="gemini-3.8-flash") is False
    assert rotator.is_account_available(acc.id, model="gemini-flash-latest") is False
    assert rotator.is_account_available(acc.id, model="gemini-3.1-pro-preview") is True

    rotator.clear_cooldown(acc.id, model="gemini-flash-latest")
    assert rotator.is_account_available(acc.id, model="gemini-3.8-flash") is True

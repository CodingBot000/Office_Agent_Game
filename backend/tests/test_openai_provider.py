import io
import json
import urllib.request

import pytest


def test_openai_report_provider_uses_report_schema(monkeypatch):
    from app.providers.openai import OpenAIReportProvider
    from app.providers.base import ReportContext
    from app.models import IncidentReportRequest
    from app.game.seed import REPORT_CRITERIA

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data)
        assert payload["text"]["format"]["name"] == "reportextraction"
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["$defs"]["ReportClaim"]["required"]) == set(schema["$defs"]["ReportClaim"]["properties"])
        return fake_api_response({"claims": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAIReportProvider(Settings(ai_provider="openai", openai_api_key="test-key", openai_model="test-model"))
    context = ReportContext(IncidentReportRequest(primary_cause="다른 원인"), REPORT_CRITERIA, ())
    assert provider.extract(context).claims == []


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"output":null}', b'{"output":[{"content":null}]}'])
def test_malformed_response_envelope_is_a_provider_failure(monkeypatch, body):
    from app.providers.openai import OpenAIStructuredExecutor
    from app.models import ReportExtraction

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(body))
    executor = OpenAIStructuredExecutor(Settings(ai_provider="openai", openai_api_key="test-key"))
    with pytest.raises(ProviderError):
        executor.run(ReportExtraction, "test")

from app.config import Settings
from app.game.seed import INCIDENT_RULES, build_initial_npcs
from app.providers.base import DecisionContext, IntentContext, ProviderError, SocialImpactContext
from app.providers.openai import OpenAIDecisionProvider, OpenAIIntentProvider, OpenAISocialImpactProvider


def fake_api_response(structured_payload: dict[str, object]) -> io.BytesIO:
    return io.BytesIO(
        json.dumps(
            {
                "status": "completed",
                "output_text": json.dumps(structured_payload, ensure_ascii=False),
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )


def test_openai_intent_provider_uses_responses_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: int):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["authorization"] = request.headers.get("Authorization")
        return fake_api_response(
            {
                "intent": "ask",
                "interaction_kind": "dialogue",
                "game_action_family": None,
                "question_type": "general_status",
                "reference_scope": "none",
                "target_npc_id": "qa_01",
                "evidence_id": None,
                "location": None,
                "confidence": 0.97,
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAIIntentProvider(
        Settings(
            ai_provider="openai",
            openai_api_key="test-key",
            openai_model="gpt-5.4-nano",
        )
    )
    intent = provider.classify(
        IntentContext(
            player_input="상황을 설명해 줘",
            current_location="qa_desk",
            target_hint="qa_01",
            available_npcs=("qa_01: QA Engineer",),
            available_npc_ids=("qa_01",),
            available_evidence_ids=("qa_warning_message",),
            discovered_evidence_ids=(),
            available_locations=("meeting_room", "dev_area", "qa_desk", "pm_desk"),
            available_actions=("ask", "move", "inspect"),
        )
    )

    assert intent.intent == "ask"
    assert intent.question_type == "general_status"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["payload"]["text"]["format"]["type"] == "json_schema"
    assert captured["authorization"] == "Bearer test-key"


def test_openai_decision_provider_validates_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int):
        return fake_api_response(
            {
                "npc_id": "qa_01",
                "emotion": "guarded",
                "stress_delta": 0,
                "trust_delta": 1,
                "cooperation_delta": 1,
                "belief_updates": [],
                "relationship_updates": [],
                "memory_candidate": None,
                "action_type": "dialogue",
                "action_target": None,
                "dialogue": "배포 전 품질 검증을 맡고 있습니다.",
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAIDecisionProvider(
        Settings(ai_provider="openai", openai_api_key="test-key", openai_model="gpt-5.4-nano")
    )
    decision = provider.decide(
        DecisionContext(
            mode="ask",
            player_input="무슨 업무를 맡았나요?",
            turn=1,
            npc=build_initial_npcs()["qa_01"],
            target_npc_id="qa_01",
            available_facts=("qa_sent_warning: QA sent a warning message before deployment.",),
            available_evidence_ids=("qa_warning_message",),
            recent_events=("TURN 1 · Player: 무슨 업무를 맡았나요?",),
            incident_rules=tuple(INCIDENT_RULES),
        )
    )

    assert decision.npc_id == "qa_01"
    assert decision.action_type == "dialogue"


def test_openai_provider_requires_api_key() -> None:
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIIntentProvider(Settings(ai_provider="openai", openai_api_key=""))


def test_openai_social_impact_provider_uses_responses_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: int):
        captured["payload"] = json.loads(request.data)
        return fake_api_response(
            {
                "action_family": "verbal_pressure",
                "direct_target_ids": ["qa_01"],
                "affected_target_ids": [],
                "object_id": None,
                "severity": 3,
                "intentionality": "deliberate",
                "observable": True,
                "evidence_based": False,
                "reason_codes": ["coercion"],
                "confidence": 0.94,
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAISocialImpactProvider(
        Settings(ai_provider="openai", openai_api_key="test-key", openai_model="gpt-5.4-nano")
    )
    impact = provider.classify_social_impact(
        SocialImpactContext(
            player_input="QA에게 당장 답하라고 윽박지른다.",
            current_location="qa_desk",
            target_hint="qa_01",
            available_npcs=("qa_01: QA Engineer",),
            available_npc_ids=("qa_01",),
            available_objects=("qa_keyboard: QA keyboard",),
            available_object_ids=("qa_keyboard",),
            recent_social_events=(),
        )
    )

    assert impact.action_family == "verbal_pressure"
    assert captured["payload"]["text"]["format"]["name"] == "socialimpactclassification"

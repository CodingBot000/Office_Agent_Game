import json
import subprocess
from pathlib import Path

import pytest


def test_cli_report_provider_uses_shared_structured_executor(monkeypatch):
    from app.providers.cli import CliReportProvider
    from app.providers.base import ReportContext
    from app.models import IncidentReportRequest, ReportExtraction
    from app.game.seed import REPORT_CRITERIA

    provider = CliReportProvider(Settings(ai_provider="cli"))
    context = ReportContext(IncidentReportRequest(primary_cause="원인이 아닙니다"), REPORT_CRITERIA, ())
    def run(model_type, prompt):
        assert model_type is ReportExtraction
        assert "원인이 아닙니다" in prompt
        assert "negated" in prompt
        return ReportExtraction(claims=[])
    monkeypatch.setattr(provider.executor, "run", run)
    assert provider.extract(context).claims == []

from app.config import Settings
from app.game.seed import build_initial_npcs, INCIDENT_RULES
from app.providers.base import DecisionContext, IntentContext, ProviderError, SocialImpactContext
from app.providers.cli import CliDecisionProvider, CliIntentProvider, CliSocialImpactProvider


def make_context() -> DecisionContext:
    return DecisionContext(
        mode="ask",
        player_input="QA에게 배포 전 문제를 질문한다.",
        turn=1,
        npc=build_initial_npcs()["qa_01"],
        target_npc_id="qa_01",
        available_facts=("qa_sent_warning: QA sent a warning message before deployment.",),
        available_evidence_ids=("qa_warning_message",),
        recent_events=("TURN 1 · Player: 경고 메시지를 보여줄 수 있나요?",),
        incident_rules=tuple(INCIDENT_RULES),
    )


def test_cli_provider_parses_structured_output_and_removes_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["env"] = kwargs["env"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "npc_id": "qa_01",
                    "emotion": "guarded",
                    "stress_delta": 0,
                    "trust_delta": 0,
                    "cooperation_delta": 0,
                    "action_type": "dialogue",
                    "dialogue": "배포 전에 위험을 보고했습니다.",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CliDecisionProvider(
        Settings(
            ai_provider="cli",
            openai_model="gpt-5.4-nano",
            ai_cli_model="gpt-5.5",
            ai_cli_command="codex",
        )
    )

    decision = provider.decide(make_context())

    assert decision.npc_id == "qa_01"
    assert decision.action_type == "dialogue"
    assert decision.dialogue == "배포 전에 위험을 보고했습니다."
    assert "--output-schema" in captured["command"]
    assert "--output-last-message" in captured["command"]
    assert "OPENAI_API_KEY" not in captured["env"]


def test_cli_provider_raises_on_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="auth failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CliDecisionProvider(Settings(ai_provider="cli"))

    with pytest.raises(ProviderError, match="CLI provider failed"):
        provider.decide(make_context())


def test_cli_intent_provider_parses_semantic_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "intent": "ask",
                    "interaction_kind": "dialogue",
                    "game_action_family": None,
                    "question_type": "general_status",
                    "reference_scope": "none",
                    "target_npc_id": "qa_01",
                    "evidence_id": None,
                    "location": None,
                    "confidence": 0.98,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CliIntentProvider(Settings(ai_provider="cli", ai_cli_model="gpt-5.5"))
    intent = provider.classify(
        IntentContext(
            player_input="상황을 설명해 줘",
            current_location="qa_desk",
            target_hint="qa_01",
            available_npcs=("qa_01: QA Engineer (QA Engineer)",),
            available_npc_ids=("qa_01",),
            available_evidence_ids=("qa_warning_message",),
            discovered_evidence_ids=(),
            available_locations=("meeting_room", "dev_area", "qa_desk", "pm_desk"),
            available_actions=("ask", "move", "inspect"),
        )
    )

    assert intent.intent == "ask"
    assert intent.question_type == "general_status"
    assert intent.target_npc_id == "qa_01"
    assert intent.confidence == 0.98


def test_cli_social_impact_provider_uses_structured_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "action_family": "property_aggression",
                    "direct_target_ids": ["qa_01"],
                    "affected_target_ids": [],
                    "object_id": "qa_keyboard",
                    "severity": 4,
                    "intentionality": "deliberate",
                    "observable": True,
                    "evidence_based": False,
                    "reason_codes": ["property_violation", "property_damage", "physical_danger"],
                    "confidence": 0.96,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = CliSocialImpactProvider(Settings(ai_provider="cli", ai_cli_model="gpt-5.6-luna"))
    impact = provider.classify_social_impact(
        SocialImpactContext(
            player_input="QA의 키보드를 빼앗아 던진다.",
            current_location="qa_desk",
            target_hint="qa_01",
            available_npcs=("qa_01: QA Engineer",),
            available_npc_ids=("qa_01",),
            available_objects=("qa_keyboard: QA keyboard",),
            available_object_ids=("qa_keyboard",),
            recent_social_events=(),
        )
    )

    assert impact.action_family == "property_aggression"
    assert impact.object_id == "qa_keyboard"
    assert impact.severity == 4

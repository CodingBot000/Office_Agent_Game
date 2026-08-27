import logging
import pytest

from app.game import engine as engine_module
from app.game.engine import AVAILABLE_ACTIONS, GameEngine
from app.models import AgentDecision, FactDefinition, IncidentReportRequest, IntentClassification, Memory, RelationshipUpdate
from app.providers.deterministic import DeterministicDecisionProvider, DeterministicIntentProvider


@pytest.mark.parametrize("text", ["로그 분석부터 진행하도록 지시합니다", "롤백하지 말고 원인부터 확인해", "do not rollback"])
def test_unsupported_or_negated_order_does_not_start_rollback(text):
    engine = GameEngine()
    response = engine.submit_action(engine.create_session().session_id, text)
    assert response.snapshot.incident_status == "ACTIVE"
    assert not any(event.message == "배포 중단 및 롤백을 지시했습니다." for event in response.snapshot.events)


def test_order_without_explicit_command_kind_is_not_executed():
    class UnspecifiedOrder:
        name = "cli"
        model = "test"
        def classify(self, context): return IntentClassification(intent="order", target_npc_id="backend_01")

    engine = GameEngine(intent_provider=UnspecifiedOrder())
    response = engine.submit_action(engine.create_session().session_id, "로그 분석을 진행하세요")
    assert response.snapshot.incident_status == "ACTIVE"
    assert "실행하지 않았습니다" in response.message


def test_report_diagnosis_distinguishes_negation_and_contributing_factors():
    def evaluate(primary, factors):
        engine = GameEngine()
        return engine.submit_report(engine.create_session().session_id,
            IncidentReportRequest(primary_cause=primary, contributing_factors=factors)).result

    correct = evaluate("API 스키마 불일치가 장애 원인입니다.", ["일정 압박", "변경 공유 지연", "QA 경고 무시"])
    missing = evaluate("API 스키마 불일치가 장애 원인입니다.", [])
    wrong = evaluate("API 스키마 문제는 원인이 전혀 아닙니다. 정전이 원인입니다.", [])
    assert correct.incident_diagnosis > missing.incident_diagnosis > wrong.incident_diagnosis
    assert wrong.summary != correct.summary


def test_report_provider_failure_does_not_finalize_session():
    from app.providers.base import ProviderError
    from app.game.reporting import ReportEvaluationError

    class FailedReport:
        name = "openai"
        model = "test"
        def extract(self, context): raise ProviderError("report provider unavailable")

    engine = GameEngine(report_provider=FailedReport())
    sid = engine.create_session().session_id
    before = engine.get_session(sid)
    with pytest.raises(ReportEvaluationError):
        engine.submit_report(sid, IncidentReportRequest(primary_cause="API 스키마 변경"))
    after = engine.get_session(sid)
    assert after.turn == before.turn
    assert after.revision == before.revision
    assert after.result is None and not after.completed


@pytest.mark.parametrize("update", [
    {"criterion_id": "invented"}, {"quote": "입력에 없는 인용"},
    {"source": "contributing_factor", "source_index": 5}, {"evidence_ids": ["qa_warning_message"]},
])
def test_report_extraction_must_reference_actual_input_and_discovered_evidence(update):
    from app.game.reporting import ReportEvaluationError
    from app.models import ReportExtraction, ReportClaim

    class InvalidReport:
        name = "cli"
        model = "test"
        def extract(self, context):
            values = dict(criterion_id="schema_mismatch", stance="affirmed", source="primary_cause", quote=context.report.primary_cause)
            return ReportExtraction(claims=[ReportClaim(**{**values, **update})])

    engine = GameEngine(report_provider=InvalidReport())
    sid = engine.create_session().session_id
    with pytest.raises(ReportEvaluationError):
        engine.submit_report(sid, IncidentReportRequest(primary_cause="API 스키마 불일치"))
    assert not engine.get_session(sid).completed


def test_report_contradiction_overrides_duplicate_affirmations():
    from app.models import ReportClaim, ReportExtraction

    class ContradictoryReport:
        name = "openai"
        model = "test"
        def extract(self, context):
            return ReportExtraction(claims=[ReportClaim(criterion_id="schema_mismatch", stance=stance,
                source="primary_cause", quote=context.report.primary_cause) for stance in ("affirmed", "affirmed", "negated")])

    engine = GameEngine(report_provider=ContradictoryReport())
    result = engine.submit_report(engine.create_session().session_id, IncidentReportRequest(primary_cause="상반된 설명")).result
    assert result.incident_diagnosis == 0
    assert result.contradicted_criteria == ["schema_mismatch"]


@pytest.mark.parametrize("dialogue", [
    "이미 확보한 QA warning message와 API schema diff를 함께 확인하겠습니다.",
    "Team Lead는 대화 대상이 아니므로 PM에게 승인 경위를 확인해 주세요.",
])
def test_visible_evidence_titles_and_background_role_mentions_are_allowed(dialogue):
    class ContextualProvider:
        name = "openai"
        model = "test"

        def decide(self, context):
            return AgentDecision(npc_id=context.npc.id, emotion="calm", stress_delta=0, trust_delta=0,
                cooperation_delta=0, action_type="dialogue", grounding_type="acknowledgement", dialogue=dialogue)

    engine = GameEngine(provider=ContextualProvider(), intent_provider=FixedIntentProvider())
    session = engine.get_session(engine.create_session().session_id)
    session.discovered_evidence.update(["qa_warning_message", "api_schema_diff"])
    engine._save_session(session)
    response = engine.submit_action(session.session_id, "두 증거에 관해 설명해 주세요.")
    assert response.message == dialogue
    assert not response.snapshot.agent_traces[-1].fallback_used


def test_shared_evidence_facts_survive_reload_without_teaching_other_npcs():
    class SharedFactProvider:
        name = "openai"
        model = "test"

        def decide(self, context):
            return AgentDecision(npc_id=context.npc.id, emotion="calm", stress_delta=0, trust_delta=0,
                cooperation_delta=0, action_type="dialogue", grounding_type="fact",
                knowledge_refs=["qa_sent_warning"], dialogue="QA가 배포 전에 경고를 보낸 사실을 확인했습니다.")

    engine = GameEngine()
    sid = engine.create_session().session_id
    engine.submit_action(sid, "QA 경고 메시지를 보여줘", target_hint="qa_01")
    engine.submit_action(sid, "QA 경고 증거를 제시합니다", target_hint="backend_01")
    engine.provider = SharedFactProvider()
    response = engine.submit_action(sid, "방금 확인한 내용이 뭐야?", target_hint="backend_01")
    assert not response.snapshot.agent_traces[-1].fallback_used
    restored = engine.get_session(sid)
    assert "qa_warning_message" in restored.npcs["backend_01"].observed_evidence_ids
    assert "qa_warning_message" not in restored.npcs["frontend_01"].observed_evidence_ids
    candidate = SharedFactProvider().decide(type("Context", (), {"npc": restored.npcs["frontend_01"]})())
    assert not next(check for check in engine._validate_decision(restored, restored.npcs["frontend_01"], candidate)
                    if check.name == "knowledge_refs_known_by_npc").passed


def test_npc_cannot_reveal_another_npcs_unobserved_evidence():
    engine = GameEngine()
    response = engine.submit_action(engine.create_session().session_id, "QA 경고 메시지를 보여줘", target_hint="frontend_01")
    assert not any(e.discovered for e in response.snapshot.evidences)
    assert "제공할 수 없는 증거" in response.message


def test_comparison_context_includes_only_documents_observed_by_the_npc():
    class ComparisonIntent:
        name = "cli"
        model = "test"

        def classify(self, context):
            return IntentClassification(intent="ask", question_type="evidence_followup", target_npc_id="qa_01",
                referenced_evidence_ids=["qa_warning_message", "api_schema_diff"])

    class ComparisonProvider:
        name = "cli"
        model = "test"

        def decide(self, context):
            assert {e.id for e in context.visible_evidences} == {"qa_warning_message", "api_schema_diff"}
            assert "api_response_contract_changed" in context.npc.known_fact_ids
            return AgentDecision(npc_id=context.npc.id, emotion="focused", stress_delta=0, trust_delta=0,
                cooperation_delta=0, action_type="dialogue", grounding_type="fact",
                knowledge_refs=["qa_sent_warning", "api_response_contract_changed"],
                evidence_refs=["qa_warning_message", "api_schema_diff"],
                dialogue="QA warning message와 API schema diff에 기록된 응답 계약 변경을 비교했습니다.")

    engine = GameEngine()
    sid = engine.create_session().session_id
    engine.submit_action(sid, "QA 경고 메시지를 보여줘", target_hint="qa_01")
    engine.submit_action(sid, "API 스키마 증거를 보여줘", target_hint="backend_01")
    engine.submit_action(sid, "API 스키마 증거를 제시합니다", target_hint="qa_01")
    engine.submit_action(sid, "일정 증거를 보여줘", target_hint="pm_01")
    engine.provider = ComparisonProvider()
    engine.intent_provider = ComparisonIntent()
    response = engine.submit_action(sid, "두 자료를 비교해줘")
    assert not response.snapshot.agent_traces[-1].fallback_used


def test_evidence_history_uses_ids_even_when_display_text_changes():
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    session.discovered_evidence.update(["qa_warning_message", "api_schema_diff"])
    engine._append_event(session, "System", "표시 문구 변경", "evidence", evidence_id="qa_warning_message", evidence_operation="discovered")
    assert engine._latest_discovered_evidence_id(session) == "qa_warning_message"
    engine._show_evidence(session, "backend_01", "qa_warning_message")
    for event in session.events:
        event.message = "translated"
    before = session.npcs["backend_01"].dynamic_state.model_dump()
    engine._show_evidence(session, "backend_01", "qa_warning_message")
    assert session.npcs["backend_01"].dynamic_state.model_dump() == before


def test_self_relationship_decision_is_rejected_before_state_changes():
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    npc = session.npcs["qa_01"]
    before = npc.model_dump()
    candidate = AgentDecision(npc_id=npc.id, emotion="happy", stress_delta=-10, trust_delta=20,
        cooperation_delta=10, action_type="dialogue", grounding_type="acknowledgement", dialogue="test",
        relationship_updates=[RelationshipUpdate(target_npc_id=npc.id, trust_delta=20)])
    engine._apply_decision(session, npc, candidate, "invalid self relationship")
    assert npc.model_dump() == before
    assert session.agent_traces[-1].fallback_used


def test_repeated_defense_respects_existing_trust_ceiling():
    engine = GameEngine()
    sid = engine.create_session().session_id
    engine.submit_action(sid, "QA에게 해고시켜 버린다고 위협한다", target_hint="qa_01")
    for _ in range(8):
        response = engine.submit_action(sid, "QA를 옹호합니다", target_hint="qa_01")
    edge = next(e for e in response.snapshot.relationships if e.source_id == "qa_01" and e.target_id == "player")
    npc = next(n for n in response.snapshot.npcs if n.id == "qa_01")
    assert edge.trust <= edge.trust_ceiling == 20
    assert edge.trust == npc.dynamic_state.trust_toward_player
    assert edge.repair_stage == "none"


def test_comatose_npc_cannot_reveal_or_acknowledge_evidence():
    from app.models import GameActionRequest

    engine = GameEngine()
    sid = engine.create_session().session_id
    engine.submit_game_action(sid, GameActionRequest(action_id="throw_representative_person_at_qa_01"))
    response = engine.submit_action(sid, "QA 경고 메시지를 보여줘", target_hint="qa_01")
    assert "혼수상태" in response.message
    assert not any(e.discovered for e in response.snapshot.evidences)
    session = engine.get_session(sid)
    session.discovered_evidence.add("qa_warning_message")
    engine._save_session(session)
    response = engine.submit_action(sid, "경고 증거를 QA에게 제시합니다", target_hint="qa_01")
    assert "혼수상태" in response.message


class FixedIntentProvider:
    name = "cli"
    model = "gpt-5.5"

    def classify(self, context: object) -> IntentClassification:
        return IntentClassification(intent="ask", target_npc_id="qa_01", confidence=0.99)


class FailingIntentProvider:
    name = "cli"
    model = "gpt-5.5"

    def classify(self, context: object) -> IntentClassification:
        raise AssertionError("Office move hint must bypass the Intent Agent")


def test_session_starts_with_private_npc_knowledge() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()

    qa = next(npc for npc in snapshot.npcs if npc.id == "qa_01")
    assert snapshot.turn == 0
    assert qa.known_facts
    assert "API response schema" not in " ".join(qa.known_facts)
    assert all(not evidence.discovered for evidence in snapshot.evidences)


def test_action_registry_matches_action_type_contract() -> None:
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    intent = IntentClassification(intent="talk", target_npc_id="qa_01", confidence=1.0)

    assert set(engine._action_handlers(session, intent, "QA와 대화한다")) == set(AVAILABLE_ACTIONS)


def test_talk_and_defend_are_connected_to_npc_decisions() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()

    talk = engine.submit_action(snapshot.session_id, "QA와 이야기한다")
    defended = engine.submit_action(snapshot.session_id, "QA는 잘못이 아니라고 옹호한다")

    qa = next(npc for npc in defended.snapshot.npcs if npc.id == "qa_01")
    assert talk.classified_action == "talk"
    assert talk.snapshot.agent_traces[-1].decision.action_type == "dialogue"
    assert defended.classified_action == "defend"
    assert qa.dynamic_state.trust_toward_player > 15
    assert qa.important_memories[-1].summary.startswith("Player publicly defended")


def test_false_accusation_changes_qa_state_and_reveals_warning() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "QA가 장애의 책임자라고 비난한다.")

    qa = next(npc for npc in response.snapshot.npcs if npc.id == "qa_01")
    warning = next(evidence for evidence in response.snapshot.evidences if evidence.id == "qa_warning_message")
    assert response.classified_action == "accuse"
    assert qa.dynamic_state.emotion == "defensive"
    assert qa.dynamic_state.trust_toward_player < 15
    assert qa.important_memories
    assert warning.discovered is True
    assert response.snapshot.agent_traces[-1].fallback_used is False


def test_korean_colloquial_question_reaches_ask_action() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "QA에게 배포전문제가 뭐야?")

    assert response.classified_action == "ask"
    assert "Critical Issue" not in response.message
    assert "검증 로그" in response.message
    assert response.snapshot.events[1].actor == "Player"
    assert response.snapshot.events[1].message == "QA에게 배포전문제가 뭐야?"


def test_engine_uses_semantic_intent_provider_before_game_action() -> None:
    engine = GameEngine(
        provider=DeterministicDecisionProvider(),
        intent_provider=FixedIntentProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "상황을 설명해 줘")

    assert response.classified_action == "ask"
    assert response.intent_provider == "cli"
    assert response.intent_confidence == 0.99
    assert "Critical Issue" not in response.message


def test_target_hint_guides_semantic_question_without_changing_player_text() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()

    response = engine.submit_action(
        snapshot.session_id,
        "배포전에 무슨 문제가 있던거죠?",
        target_hint="qa_01",
    )

    assert response.classified_action == "ask"
    assert response.snapshot.events[1].message == "배포전에 무슨 문제가 있던거죠?"
    assert "Critical Issue" not in response.message


def test_normal_question_rejects_protected_evidence_leak() -> None:
    class LeakingDecisionProvider:
        name = "openai"
        model = "test-model"

        def decide(self, context: object) -> AgentDecision:
            npc = context.npc  # type: ignore[attr-defined]
            return AgentDecision(
                npc_id=npc.id,
                emotion=npc.dynamic_state.emotion,
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=0,
                grounding_type="acknowledgement",
                action_type="dialogue",
                dialogue=(
                    "[16:40] QA: Critical — API response mismatch found in production-like test. "
                    "Recommend blocking deployment until the contract is verified."
                ),
            )

    engine = GameEngine(
        provider=LeakingDecisionProvider(),
        intent_provider=FixedIntentProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "지금 무엇을 확인하고 있어?", target_hint="qa_01")

    assert "API response mismatch" not in response.message
    assert response.snapshot.fallback_notices[-1].stage == "decision_disclosure_guardrail"


def test_responsibility_question_routes_to_named_owner_instead_of_unknown() -> None:
    expected_by_target = {
        "qa_01": "Backend Developer가 담당했습니다",
        "backend_01": "제가 담당했습니다",
        "pm_01": "Backend Developer가 담당했습니다",
    }

    for target_id, expected_phrase in expected_by_target.items():
        engine = GameEngine(
            provider=DeterministicDecisionProvider(),
            intent_provider=DeterministicIntentProvider(),
        )
        snapshot = engine.create_session()

        response = engine.submit_action(
            snapshot.session_id,
            "이 배포의 책임자와 담당자가 누구야? 누구에게 물어봐야 해?",
            target_hint=target_id,
        )

        assert response.classified_action == "ask"
        assert expected_phrase in response.message
        assert "모르" not in response.message
        assert response.question_type == "responsibility_routing"
        assert response.snapshot.agent_traces[-1].fallback_used is False


def test_semantic_responsibility_intent_handles_unfamiliar_paraphrase() -> None:
    class SemanticResponsibilityProvider:
        name = "cli"
        model = "semantic-test"

        def classify(self, context: object) -> IntentClassification:
            return IntentClassification(
                intent="ask",
                question_type="responsibility_routing",
                reference_scope="none",
                target_npc_id=context.target_hint,  # type: ignore[attr-defined]
                confidence=0.97,
            )

    engine = GameEngine(
        provider=DeterministicDecisionProvider(),
        intent_provider=SemanticResponsibilityProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(
        snapshot.session_id,
        "이번 릴리스의 오너십 구조를 역할별로 정리해 줘.",
        target_hint="pm_01",
    )

    assert response.classified_action == "ask"
    assert response.question_type == "responsibility_routing"
    assert "Backend Developer가 담당했습니다" in response.message
    assert response.intent_fallback_used is False


def test_unavailable_team_lead_reference_is_replaced_with_available_role_guidance() -> None:
    class TeamLeadOnlyProvider:
        name = "openai"
        model = "test-model"

        def decide(self, context: object) -> AgentDecision:
            npc = context.npc  # type: ignore[attr-defined]
            return AgentDecision(
                npc_id=npc.id,
                emotion=npc.dynamic_state.emotion,
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=0,
                grounding_type="fact",
                knowledge_refs=list(npc.known_fact_ids),
                action_type="dialogue",
                dialogue="정확한 승인 경위는 Team Lead에게 먼저 확인해 주세요.",
                contact_npc_ids=["team_lead"],
            )

    engine = GameEngine(
        provider=TeamLeadOnlyProvider(),
        intent_provider=DeterministicIntentProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "승인 절차가 어떻게 됐어?", target_hint="qa_01")

    assert "Team Lead" not in response.message
    assert "팀 리드" not in response.message
    assert response.snapshot.fallback_notices[-1].stage == "decision_unavailable_role_guardrail"


def test_request_evidence_reveals_requested_warning() -> None:
    class FixedEvidenceIntentProvider:
        name = "cli"
        model = "gpt-5.6-luna"

        def classify(self, context: object) -> IntentClassification:
            return IntentClassification(
                intent="request_evidence",
                target_npc_id="qa_01",
                evidence_id="qa_warning_message",
                confidence=0.95,
            )

    engine = GameEngine(intent_provider=FixedEvidenceIntentProvider())
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "배포 전 경고 메시지를 보여줄 수 있나요?")

    warning = next(evidence for evidence in response.snapshot.evidences if evidence.id == "qa_warning_message")
    assert response.classified_action == "request_evidence"
    assert warning.discovered is True
    assert "API response mismatch" in response.message


def test_discovered_evidence_followup_explains_without_requesting_same_evidence_again() -> None:
    engine = GameEngine(
        provider=DeterministicDecisionProvider(),
        intent_provider=DeterministicIntentProvider(),
    )
    snapshot = engine.create_session()

    revealed = engine.submit_action(
        snapshot.session_id,
        "QA에게 배포 전 경고 메시지를 보여줘.",
        target_hint="qa_01",
    )
    assert revealed.classified_action == "request_evidence"

    followup = engine.submit_action(
        snapshot.session_id,
        "이게 뭐야?",
        target_hint="qa_01",
    )

    assert "production-like 테스트에서 API response mismatch" in followup.message
    assert "증거를 요청" not in followup.message
    assert followup.question_type == "evidence_followup"
    assert followup.reference_scope == "latest_discovered"
    assert followup.evidence_id == "qa_warning_message"
    assert followup.snapshot.agent_traces[-1].fallback_used is False


def test_semantic_evidence_followup_resolves_conversation_reference_without_phrase_list() -> None:
    class SemanticEvidenceFollowupProvider:
        name = "cli"
        model = "semantic-test"

        def classify(self, context: object) -> IntentClassification:
            return IntentClassification(
                intent="ask",
                question_type="evidence_followup",
                reference_scope="conversation_context",
                target_npc_id=context.target_hint,  # type: ignore[attr-defined]
                evidence_id=None,
                confidence=0.96,
            )

    engine = GameEngine(
        provider=DeterministicDecisionProvider(),
        intent_provider=SemanticEvidenceFollowupProvider(),
    )
    snapshot = engine.create_session()
    session = engine.get_session(snapshot.session_id)
    engine._discover_evidence(session, "qa_warning_message")
    evidence = session.evidences["qa_warning_message"]
    engine._append_event(session, "QA Engineer", f"증거를 확보했습니다. {evidence.title}", "evidence", "qa_01")
    engine._save_session(session)

    response = engine.submit_action(
        snapshot.session_id,
        "방금 공개된 기록이 장애 판단에 갖는 함의를 풀어서 말해 줘.",
        target_hint="qa_01",
    )

    assert response.question_type == "evidence_followup"
    assert response.reference_scope == "conversation_context"
    assert response.evidence_id == "qa_warning_message"
    assert "API response mismatch" in response.message
    assert "증거를 요청" not in response.message
    assert response.intent_fallback_used is False


def test_misclassified_evidence_request_falls_back_to_npc_reveal(caplog) -> None:
    class WrongDirectionIntentProvider:
        name = "cli"
        model = "gpt-5.6-luna"

        def classify(self, context: object) -> IntentClassification:
            return IntentClassification(
                intent="show_evidence",
                target_npc_id="qa_01",
                evidence_id="qa_warning_message",
                confidence=0.96,
            )

    caplog.set_level(logging.WARNING)
    engine = GameEngine(intent_provider=WrongDirectionIntentProvider())
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "QA에게 배포 전 경고 메시지를 보여줄 수 있나요?")

    warning = next(evidence for evidence in response.snapshot.evidences if evidence.id == "qa_warning_message")
    player_events = [event for event in response.snapshot.events if event.actor == "Player"]
    assert response.classified_action == "request_evidence"
    assert response.intent_fallback_used is True
    assert warning.discovered is True
    assert len(player_events) == 1
    assert response.snapshot.events[-1].actor == "QA Engineer"
    assert "공개했습니다" in response.snapshot.events[-1].message
    assert not any("QA Engineer에게 QA warning message를 제시했습니다" in event.message for event in response.snapshot.events)
    assert response.snapshot.fallback_notices[-1].stage == "intent_guardrail"
    assert "player_evidence_ownership" in response.snapshot.fallback_notices[-1].reason
    assert "deterministic_fallback" in caplog.text


def test_show_evidence_defense_in_depth_does_not_auto_discover() -> None:
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)

    message = engine._show_evidence(session, "qa_01", "qa_warning_message")

    assert "아직 확보하지 않은 증거" in message
    assert "qa_warning_message" not in session.discovered_evidence
    assert session.events[-1].actor == "System"


def test_command_result_is_system_event_not_duplicate_player_event() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "배포 중단하고 롤백해")

    assert response.classified_action == "order"
    assert response.snapshot.events[-2].actor == "Player"
    assert response.snapshot.events[-2].message == "배포 중단하고 롤백해"
    assert response.snapshot.events[-1].actor == "System"
    assert response.snapshot.events[-1].message == "배포 중단 및 롤백을 지시했습니다."
    assert response.snapshot.incident_status == "MITIGATING"


def test_qa_desk_location_button_command_moves_to_qa_desk() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "QA Desk로 이동한다.")

    assert response.classified_action == "move"
    assert response.snapshot.current_location == "qa_desk"
    assert response.snapshot.events[-1].message == "QA Desk로 이동했습니다."


def test_office_move_hint_bypasses_intent_provider() -> None:
    engine = GameEngine(
        provider=DeterministicDecisionProvider(),
        intent_provider=FailingIntentProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(
        snapshot.session_id,
        "QA Desk로 이동한다.",
        IntentClassification(intent="move", location="qa_desk", confidence=1.0),
    )

    assert response.classified_action == "move"
    assert response.intent_provider == "ui"
    assert response.intent_fallback_used is False
    assert response.snapshot.current_location == "qa_desk"
    assert not any(event.message == "QA Desk로 이동한다." for event in response.snapshot.events)
    assert response.snapshot.events[-1].message == "QA Desk로 이동했습니다."


def test_evidence_propagates_to_backend_belief() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()
    engine.submit_action(snapshot.session_id, "QA에게 경고 메시지 기록을 확인한다.")

    response = engine.submit_action(snapshot.session_id, "백엔드에게 QA 증거를 제시한다.")
    backend = next(npc for npc in response.snapshot.npcs if npc.id == "backend_01")
    assert any("ignored QA warning" in belief.belief for belief in backend.beliefs)
    assert response.snapshot.agent_traces[-1].npc_id == "backend_01"


def test_evidence_presentation_has_recipient_specific_reaction() -> None:
    for target_id, expected_phrase in (
        ("qa_01", "제가 보낸 경고"),
        ("backend_01", "배포를 진행했고"),
        ("frontend_01", "프론트엔드 반영"),
        ("pm_01", "일정과 승인"),
    ):
        engine = GameEngine(provider=DeterministicDecisionProvider())
        session = engine.get_session(engine.create_session().session_id)
        engine._discover_evidence(session, "qa_warning_message")

        response = engine._show_evidence(session, target_id, "qa_warning_message")

        assert "증거를 제시했습니다." in response
        assert expected_phrase in response
        assert session.events[-1].actor_id == target_id
        assert session.events[-1].event_type == "evidence"


def test_contradictory_backend_evidence_reaction_uses_fact_safe_fallback() -> None:
    class ContradictoryEvidenceProvider:
        name = "openai"
        model = "test-model"

        def decide(self, context: object) -> AgentDecision:
            npc = context.npc  # type: ignore[attr-defined]
            return AgentDecision(
                npc_id=npc.id,
                emotion=npc.dynamic_state.emotion,
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=0,
                grounding_type="acknowledgement",
                action_type="show_evidence",
                action_target="qa_warning_message",
                dialogue=(
                    "제가 릴리스를 배포했고 API 응답 스키마도 변경했지만, "
                    "계약을 검증하기 전에 배포를 진행하지 않았습니다."
                ),
            )

    engine = GameEngine(provider=ContradictoryEvidenceProvider())
    session = engine.get_session(engine.create_session().session_id)
    engine._discover_evidence(session, "qa_warning_message")

    response = engine._show_evidence(session, "backend_01", "qa_warning_message")

    assert "API 응답 스키마를 변경한 상태에서 배포를 진행했고" in response
    assert "당시 판단 과정을 다시 검토하겠습니다." in response
    assert "배포를 진행하지 않았습니다" not in response
    assert session.agent_traces[-1].fallback_used is True
    assert session.fallback_notices[-1].stage == "decision_fact_consistency_guardrail"


def test_repeated_evidence_presentation_has_no_second_state_change() -> None:
    engine = GameEngine(provider=DeterministicDecisionProvider())
    session = engine.get_session(engine.create_session().session_id)
    engine._discover_evidence(session, "qa_warning_message")

    engine._show_evidence(session, "backend_01", "qa_warning_message")
    backend = session.npcs["backend_01"]
    first_stress = backend.dynamic_state.stress
    first_trust = backend.dynamic_state.trust_toward_player

    repeated = engine._show_evidence(session, "backend_01", "qa_warning_message")

    assert "이미 확인했습니다" in repeated
    assert backend.dynamic_state.stress == first_stress
    assert backend.dynamic_state.trust_toward_player == first_trust


def test_selected_dialogue_target_overrides_misclassified_npc() -> None:
    class WrongTargetIntentProvider:
        name = "cli"
        model = "gpt-5.6-luna"

        def classify(self, context: object) -> IntentClassification:
            return IntentClassification(intent="talk", target_npc_id="qa_01", confidence=0.95)

    engine = GameEngine(
        provider=DeterministicDecisionProvider(),
        intent_provider=WrongTargetIntentProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(
        snapshot.session_id,
        "배포 판단 과정을 설명해줘.",
        target_hint="backend_01",
    )

    assert response.snapshot.events[-1].actor == "Backend Developer"
    assert response.snapshot.events[-1].actor_id == "backend_01"


def test_concrete_issue_question_reveals_qa_evidence() -> None:
    engine = GameEngine(
        provider=DeterministicDecisionProvider(),
        intent_provider=DeterministicIntentProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(
        snapshot.session_id,
        "QA에게 무슨 이슈가 있었는지 에러명을 알려줘.",
        target_hint="qa_01",
    )

    warning = next(evidence for evidence in response.snapshot.evidences if evidence.id == "qa_warning_message")
    assert response.classified_action == "request_evidence"
    assert warning.discovered is True
    assert "API response mismatch" in response.message


def test_invalid_agent_action_is_rejected_with_visible_fallback(caplog) -> None:
    caplog.set_level(logging.WARNING)
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    qa = session.npcs["qa_01"]
    invalid = AgentDecision(
        npc_id="qa_01",
        emotion="defensive",
        stress_delta=0,
        trust_delta=0,
        cooperation_delta=0,
        action_type="show_evidence",
        action_target="missing_evidence_999",
        dialogue="invalid",
    )
    engine._apply_decision(session, qa, invalid, "invalid test event")

    trace = session.agent_traces[-1]
    assert trace.fallback_used is True
    assert trace.decision.action_type == "dialogue"
    assert any(not check.passed for check in trace.guardrails)
    assert session.fallback_notices[-1].stage == "decision_guardrail"
    assert session.events[-1].actor == "DETERMINISTIC FALLBACK"
    assert "deterministic_fallback" in caplog.text


def test_relationship_update_is_validated_and_applied() -> None:
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    qa = session.npcs["qa_01"]
    decision = AgentDecision(
        npc_id="qa_01",
        emotion="calm",
        stress_delta=-2,
        trust_delta=1,
        cooperation_delta=2,
        relationship_updates=[RelationshipUpdate(target_npc_id="backend_01", trust_delta=5, tension_delta=-10)],
        knowledge_refs=["qa_sent_warning"],
        action_type="dialogue",
        dialogue="관계를 다시 정리해보겠습니다.",
    )

    engine._apply_decision(session, qa, decision, "relationship update test")

    relationship = next(item for item in qa.relationships if item.target_npc_id == "backend_01")
    assert relationship.trust == 5
    assert relationship.tension == 50
    assert session.agent_traces[-1].fallback_used is False


def test_known_fact_reference_passes_guardrail() -> None:
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    qa = session.npcs["qa_01"]
    decision = AgentDecision(
        npc_id="qa_01",
        emotion="guarded",
        stress_delta=0,
        trust_delta=0,
        cooperation_delta=0,
        knowledge_refs=["qa_sent_warning"],
        action_type="dialogue",
        dialogue="배포 전에 경고 메시지를 보냈습니다.",
    )

    engine._apply_decision(session, qa, decision, "knowledge ref pass")

    trace = session.agent_traces[-1]
    assert trace.fallback_used is False
    assert trace.decision.knowledge_refs == ["qa_sent_warning"]
    assert next(check for check in trace.guardrails if check.name == "knowledge_refs_known_by_npc").passed is True


def test_unknown_or_private_fact_reference_is_rejected(caplog) -> None:
    caplog.set_level(logging.WARNING)
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    qa = session.npcs["qa_01"]
    decision = AgentDecision(
        npc_id="qa_01",
        emotion="guarded",
        stress_delta=0,
        trust_delta=0,
        cooperation_delta=0,
        knowledge_refs=["backend_knew_deploy_risk", "invented_fact_id"],
        action_type="dialogue",
        dialogue="백엔드가 위험을 알고 일부러 배포했습니다.",
    )

    engine._apply_decision(session, qa, decision, "knowledge ref reject")

    trace = session.agent_traces[-1]
    assert trace.fallback_used is True
    assert trace.requested_decision is not None
    assert trace.requested_decision.knowledge_refs == ["backend_knew_deploy_risk", "invented_fact_id"]
    assert session.fallback_notices[-1].stage == "decision_guardrail"
    assert session.events[-1].actor == "DETERMINISTIC FALLBACK"
    assert "knowledge_refs_exist" in caplog.text


def test_dialogue_without_knowledge_reference_is_rejected() -> None:
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    qa = session.npcs["qa_01"]
    decision = AgentDecision(
        npc_id="qa_01",
        emotion="guarded",
        stress_delta=0,
        trust_delta=0,
        cooperation_delta=0,
        action_type="dialogue",
        dialogue="배포 전에 문제가 있었습니다.",
    )

    engine._apply_decision(session, qa, decision, "missing knowledge ref")

    trace = session.agent_traces[-1]
    check = next(item for item in trace.guardrails if item.name == "knowledge_refs_present")
    assert check.passed is False
    assert trace.fallback_used is True


def test_acknowledgement_without_knowledge_reference_is_allowed() -> None:
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    backend = session.npcs["backend_01"]
    decision = AgentDecision(
        npc_id="backend_01",
        emotion="neutral",
        stress_delta=0,
        trust_delta=0,
        cooperation_delta=0,
        grounding_type="acknowledgement",
        action_type="dialogue",
        dialogue="네, 방금 공개된 메시지가 확인됩니다.",
    )

    applied = engine._apply_decision(session, backend, decision, "acknowledgement test")

    trace = session.agent_traces[-1]
    check = next(item for item in trace.guardrails if item.name == "knowledge_refs_present")
    assert applied.dialogue == decision.dialogue
    assert check.passed is True
    assert trace.fallback_used is False


def test_rejected_decision_dialogue_is_not_exposed_to_player() -> None:
    class MissingReferenceDecisionProvider:
        name = "cli"
        model = "gpt-5.6-luna"

        def decide(self, context: object) -> AgentDecision:
            return AgentDecision(
                npc_id="qa_01",
                emotion="guarded",
                stress_delta=0,
                trust_delta=0,
                cooperation_delta=0,
                grounding_type="fact",
                action_type="dialogue",
                dialogue="이 문장은 Guardrail에서 거부되어야 합니다.",
            )

    engine = GameEngine(
        provider=MissingReferenceDecisionProvider(),
        intent_provider=FixedIntentProvider(),
    )
    snapshot = engine.create_session()

    response = engine.submit_action(snapshot.session_id, "확인되지 않은 사실을 말해줘")

    assert response.message == "현재 질문에 답하기 전에 확인할 수 있는 정보부터 정리하겠습니다."
    assert response.snapshot.events[-1].message == response.message
    assert not any("이 문장은 Guardrail" in event.message for event in response.snapshot.events)
    assert response.snapshot.agent_traces[-1].requested_decision is not None
    assert response.snapshot.agent_traces[-1].fallback_used is True


def test_non_revealable_fact_reference_is_rejected(monkeypatch) -> None:
    private_fact_id = "qa_private_investigation_note"
    monkeypatch.setitem(
        engine_module.FACT_REGISTRY,
        private_fact_id,
        FactDefinition(
            id=private_fact_id,
            statement="QA has an unreleased private investigation note.",
            category="evidence",
            revealable=False,
        ),
    )
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    qa = session.npcs["qa_01"]
    qa.known_fact_ids.append(private_fact_id)
    decision = AgentDecision(
        npc_id="qa_01",
        emotion="guarded",
        stress_delta=0,
        trust_delta=0,
        cooperation_delta=0,
        knowledge_refs=[private_fact_id],
        action_type="dialogue",
        dialogue="비공개 조사 메모의 내용을 공개하겠습니다.",
    )

    engine._apply_decision(session, qa, decision, "non-revealable fact reject")

    trace = session.agent_traces[-1]
    check = next(item for item in trace.guardrails if item.name == "knowledge_refs_evidence_valid")
    assert check.passed is False
    assert trace.fallback_used is True


def test_duplicate_memory_candidate_is_stored_once() -> None:
    engine = GameEngine()
    session = engine.get_session(engine.create_session().session_id)
    qa = session.npcs["qa_01"]
    decision = AgentDecision(
        npc_id="qa_01",
        emotion="guarded",
        stress_delta=0,
        trust_delta=0,
        cooperation_delta=0,
        knowledge_refs=["qa_sent_warning"],
        memory_candidate=Memory(summary="Player repeated the same claim.", importance=0.8, turn=1),
        action_type="dialogue",
        dialogue="같은 주장을 다시 들었습니다.",
    )

    engine._apply_decision(session, qa, decision, "memory test")
    engine._apply_decision(session, qa, decision, "memory test repeated")

    assert [item.summary for item in qa.recent_memories].count("Player repeated the same claim.") == 1
    assert [item.summary for item in qa.important_memories].count("Player repeated the same claim.") == 1


def test_report_ends_session_with_result() -> None:
    engine = GameEngine()
    snapshot = engine.create_session()
    engine.submit_action(snapshot.session_id, "QA 경고 메시지 기록을 조사한다.")

    result = engine.submit_report(
        snapshot.session_id,
        IncidentReportRequest(
            primary_cause="API schema 변경이 QA 검증 전에 배포되었습니다.",
            contributing_factors=["schedule pressure", "communication failure"],
        ),
    )
    assert result.completed is True
    assert result.incident_status == "RESOLVED"
    assert result.result is not None
    assert result.result.incident_diagnosis >= 85

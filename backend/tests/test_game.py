import logging

from app.game import engine as engine_module
from app.game.engine import AVAILABLE_ACTIONS, GameEngine
from app.models import AgentDecision, FactDefinition, IncidentReportRequest, IntentClassification, Memory, RelationshipUpdate
from app.providers.deterministic import DeterministicDecisionProvider


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
    assert "Critical Issue" in response.message
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
    assert "Critical Issue" in response.message


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
    assert "Critical Issue" in response.message


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

from app.game.engine import GameEngine
from app.models import AgentDecision, IncidentReportRequest, IntentClassification
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


def test_invalid_agent_action_is_rejected_with_fallback() -> None:
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

import logging

from app.game.engine import GameEngine
from app.models import IntentClassification, SocialImpactClassification
from app.providers.base import ProviderError


class SocialIntentProvider:
    name = "cli"
    model = "gpt-5.6-luna"

    def classify(self, context: object) -> IntentClassification:
        if "업무" in getattr(context, "player_input", ""):
            return IntentClassification(intent="ask", target_npc_id="qa_01", confidence=0.99)
        return IntentClassification(intent="social_action", target_npc_id="qa_01", confidence=0.99)


class InvalidObjectSocialProvider:
    name = "cli"
    model = "gpt-5.6-luna"

    def classify_social_impact(self, context: object) -> SocialImpactClassification:
        return SocialImpactClassification(
            action_family="property_aggression",
            direct_target_ids=["qa_01"],
            affected_target_ids=[],
            object_id="invented_keyboard",
            severity=4,
            intentionality="deliberate",
            observable=True,
            evidence_based=False,
            reason_codes=["property_violation", "property_damage"],
            confidence=0.9,
        )


class FailingSocialProvider:
    name = "cli"
    model = "gpt-5.6-luna"

    def classify_social_impact(self, context: object) -> SocialImpactClassification:
        raise ProviderError("social classifier unavailable")


def move_to_qa_desk(engine: GameEngine, session_id: str) -> None:
    engine.submit_action(
        session_id,
        "QA Desk로 이동한다.",
        IntentClassification(intent="move", location="qa_desk", confidence=1.0),
    )


def test_keyboard_throw_applies_relationship_object_and_hr_policy() -> None:
    engine = GameEngine(intent_provider=SocialIntentProvider())
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    response = engine.submit_action(started.session_id, "QA의 키보드를 빼앗아 던진다.")

    qa_relationship = next(
        relationship
        for relationship in response.snapshot.relationships
        if relationship.source_id == "qa_01" and relationship.target_id == "player"
    )
    qa = next(npc for npc in response.snapshot.npcs if npc.id == "qa_01")
    keyboard = next(item for item in response.snapshot.world_objects if item.id == "qa_keyboard")
    social_trace = response.snapshot.social_events[-1]
    assert response.classified_action == "social_action"
    assert response.social_impact_fallback_used is False
    assert social_trace.classification.action_family == "property_aggression"
    assert qa_relationship.trust < 15
    assert qa_relationship.fear >= 20
    assert qa_relationship.trust_ceiling == 20
    assert qa_relationship.repair_stage == "none"
    assert keyboard.condition == "damaged"
    assert response.snapshot.incident_status == "HR_ESCALATED"
    assert qa.important_memories


def test_observable_verbal_pressure_affects_witnesses_less_than_target() -> None:
    engine = GameEngine(intent_provider=SocialIntentProvider())
    started = engine.create_session()

    response = engine.submit_action(started.session_id, "QA에게 모두가 보는 앞에서 당장 답하라고 윽박지른다.")

    trace = response.snapshot.social_events[-1]
    direct = next(effect for effect in trace.policy_outcome.relationship_effects if effect.source_id == "qa_01")
    witnesses = [
        effect
        for effect in trace.policy_outcome.relationship_effects
        if "witness" in effect.reason_codes
    ]
    assert trace.classification.action_family == "verbal_pressure"
    assert {effect.source_id for effect in witnesses} == {"backend_01", "frontend_01", "pm_01"}
    assert all(abs(effect.trust_delta) < abs(direct.trust_delta) for effect in witnesses)


def test_invalid_semantic_object_uses_visible_deterministic_fallback(caplog) -> None:
    caplog.set_level(logging.WARNING)
    engine = GameEngine(
        intent_provider=SocialIntentProvider(),
        social_impact_provider=InvalidObjectSocialProvider(),
    )
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    response = engine.submit_action(started.session_id, "QA의 키보드를 빼앗아 던진다.")

    trace = response.snapshot.social_events[-1]
    assert response.social_impact_fallback_used is True
    assert trace.requested_classification is not None
    assert trace.requested_classification.object_id == "invented_keyboard"
    assert trace.classification.object_id == "qa_keyboard"
    assert response.snapshot.fallback_notices[-1].stage == "social_impact_guardrail"
    assert "deterministic_fallback" in caplog.text


def test_social_provider_failure_uses_visible_deterministic_fallback() -> None:
    engine = GameEngine(
        intent_provider=SocialIntentProvider(),
        social_impact_provider=FailingSocialProvider(),
    )
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    response = engine.submit_action(started.session_id, "QA에게 당장 답하라고 윽박지른다.")

    assert response.social_impact_fallback_used is True
    assert response.snapshot.social_events[-1].classification.action_family == "verbal_pressure"
    assert response.snapshot.fallback_notices[-1].stage == "social_impact_provider"


def test_physical_assault_blocks_dialogue_until_recovery() -> None:
    engine = GameEngine(intent_provider=SocialIntentProvider())
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    assault = engine.submit_action(started.session_id, "QA를 주먹으로 때린다.")
    refused = engine.submit_action(started.session_id, "QA에게 무슨 업무를 맡았는지 묻는다.")

    assert assault.snapshot.incident_status == "SECURITY_ESCALATED"
    assert "qa_01" in assault.snapshot.dialogue_refused_npc_ids
    assert "정상적인 대화를 거부" in refused.message


def test_apology_repair_and_mediation_restore_in_stages() -> None:
    engine = GameEngine(intent_provider=SocialIntentProvider())
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)
    engine.submit_action(started.session_id, "QA의 키보드를 빼앗아 던진다.")

    apologized = engine.submit_action(started.session_id, "QA에게 진심으로 사과한다.")
    repaired = engine.submit_action(started.session_id, "QA에게 새 키보드로 보상하고 피해를 복구한다.")
    mediated = engine.submit_action(started.session_id, "QA와 공식적인 중재를 진행한다.")

    def qa_relationship(response):
        return next(
            edge
            for edge in response.snapshot.relationships
            if edge.source_id == "qa_01" and edge.target_id == "player"
        )

    assert qa_relationship(apologized).repair_stage == "apologized"
    assert qa_relationship(apologized).trust_ceiling == 20
    assert qa_relationship(repaired).repair_stage == "repaired"
    assert qa_relationship(repaired).trust_ceiling == 20
    assert qa_relationship(mediated).repair_stage == "mediated"
    assert qa_relationship(mediated).trust_ceiling is None
    assert qa_relationship(mediated).fear_floor == 0

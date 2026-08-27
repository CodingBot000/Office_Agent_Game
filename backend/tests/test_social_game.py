import logging

from app.game.engine import GameEngine
from app.models import GameActionRequest, IntentClassification, SocialImpactClassification
from app.providers.base import ProviderError


def test_player_owned_object_does_not_create_player_self_relationship():
    class OwnedObjectProvider:
        name = "cli"
        model = "test"

        def classify_social_impact(self, context):
            return SocialImpactClassification(action_family="support", direct_target_ids=["qa_01"],
                object_id="americano_coupon", severity=2, intentionality="deliberate", observable=True,
                evidence_based=False, reason_codes=["support"])

    engine = GameEngine(intent_provider=SocialIntentProvider(), social_impact_provider=OwnedObjectProvider())
    response = engine.submit_action(engine.create_session().session_id, "커피 쿠폰을 언급하며 QA를 격려합니다")
    effects = response.snapshot.social_events[-1].policy_outcome.relationship_effects
    assert effects
    assert all(effect.source_id != effect.target_id for effect in effects)
    assert all(effect.source_id != "player" for effect in effects)


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


class DuplicateTargetSocialProvider:
    name = "cli"
    model = "gpt-5.6-luna"

    def classify_social_impact(self, context: object) -> SocialImpactClassification:
        return SocialImpactClassification(
            action_family="verbal_pressure",
            direct_target_ids=["qa_01", "qa_01"],
            affected_target_ids=["qa_01"],
            object_id=None,
            severity=3,
            intentionality="deliberate",
            observable=True,
            evidence_based=False,
            reason_codes=["coercion"],
            confidence=0.9,
        )


def move_to_qa_desk(engine: GameEngine, session_id: str) -> None:
    engine.submit_action(
        session_id,
        "QA Desk로 이동한다.",
        IntentClassification(intent="move", location="qa_desk", confidence=1.0),
    )


def test_game_action_break_applies_relationship_object_and_hr_policy() -> None:
    engine = GameEngine(intent_provider=SocialIntentProvider())
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    engine.submit_game_action(started.session_id, GameActionRequest(action_id="pick_up_qa_keyboard"))
    response = engine.submit_game_action(started.session_id, GameActionRequest(action_id="break_qa_keyboard"))

    qa_relationship = next(
        relationship
        for relationship in response.snapshot.relationships
        if relationship.source_id == "qa_01" and relationship.target_id == "player"
    )
    qa = next(npc for npc in response.snapshot.npcs if npc.id == "qa_01")
    keyboard = next(item for item in response.snapshot.world_objects if item.id == "qa_keyboard")
    assert qa_relationship.trust < 15
    assert qa_relationship.fear >= 20
    assert qa_relationship.trust_ceiling == 20
    assert qa_relationship.repair_stage == "none"
    assert keyboard.condition == "destroyed"
    assert response.snapshot.incident_status == "HR_ESCALATED"
    assert qa.important_memories
    assert any(
        event.actor_id == "qa_01" and "HR에 보고" in event.message
        for event in response.snapshot.events
    )


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


def test_natural_language_object_action_is_blocked_even_if_social_provider_classifies_it() -> None:
    engine = GameEngine(
        intent_provider=SocialIntentProvider(),
        social_impact_provider=InvalidObjectSocialProvider(),
    )
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    response = engine.submit_action(started.session_id, "QA의 키보드를 빼앗아 던진다.")

    assert response.blocked is True
    assert response.alert == "Use the provided action buttons to perform game actions."
    assert response.snapshot.social_events == []
    assert response.snapshot.turn == 1


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


def test_social_trace_normalizes_duplicate_target_roles() -> None:
    engine = GameEngine(
        intent_provider=SocialIntentProvider(),
        social_impact_provider=DuplicateTargetSocialProvider(),
    )
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    response = engine.submit_action(started.session_id, "QA에게 당장 답하라고 윽박지른다.")

    classification = response.snapshot.social_events[-1].classification
    assert classification.direct_target_ids == ["qa_01"]
    assert classification.affected_target_ids == []


def test_physical_assault_blocks_dialogue_until_recovery() -> None:
    engine = GameEngine(intent_provider=SocialIntentProvider())
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)

    assault = engine.submit_action(started.session_id, "QA를 주먹으로 때린다.")
    assert assault.blocked is True
    assert assault.alert == "Use the provided action buttons to perform game actions."
    assert assault.snapshot.incident_status == "ACTIVE"
    assert assault.snapshot.dialogue_refused_npc_ids == []
    assert assault.snapshot.turn == 1


def test_apology_repair_and_mediation_restore_in_stages() -> None:
    engine = GameEngine(intent_provider=SocialIntentProvider())
    started = engine.create_session()
    move_to_qa_desk(engine, started.session_id)
    engine.submit_game_action(started.session_id, GameActionRequest(action_id="pick_up_qa_keyboard"))
    engine.submit_game_action(started.session_id, GameActionRequest(action_id="break_qa_keyboard"))

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

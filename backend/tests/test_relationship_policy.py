from app.game.relationship_policy import RelationshipPolicyEngine
from app.models import SocialImpactClassification


def make_impact(action_family: str, severity: int, *, observable: bool = True) -> SocialImpactClassification:
    return SocialImpactClassification(
        action_family=action_family,
        direct_target_ids=["qa_01"],
        affected_target_ids=[],
        object_id="qa_keyboard" if action_family in {"property_interference", "property_aggression"} else None,
        severity=severity,
        intentionality="deliberate",
        observable=observable,
        evidence_based=False,
        reason_codes=["property_damage"] if action_family == "property_aggression" else [],
        confidence=0.9,
    )


def test_property_aggression_has_stronger_policy_effect_than_verbal_pressure() -> None:
    engine = RelationshipPolicyEngine()
    property_outcome = engine.evaluate(
        make_impact("property_aggression", 4),
        actor_id="player",
        direct_target_ids=["qa_01"],
        object_owner_id="qa_01",
        turn=1,
    )
    verbal_outcome = engine.evaluate(
        make_impact("verbal_pressure", 3),
        actor_id="player",
        direct_target_ids=["qa_01"],
        turn=1,
    )

    property_effect = property_outcome.relationship_effects[0]
    verbal_effect = verbal_outcome.relationship_effects[0]
    assert property_outcome.conduct_level == "severe_misconduct"
    assert property_effect.trust_delta < verbal_effect.trust_delta
    assert property_effect.fear_delta > verbal_effect.fear_delta
    assert {event.event_type for event in property_outcome.mandatory_world_events} == {
        "object_damaged",
        "hr_escalated",
    }


def test_role_priority_prevents_duplicate_relationship_effects() -> None:
    outcome = RelationshipPolicyEngine().evaluate(
        make_impact("property_aggression", 4),
        actor_id="player",
        direct_target_ids=["qa_01"],
        object_owner_id="qa_01",
        witness_ids=["qa_01", "frontend_01"],
        turn=1,
    )

    assert [effect.source_id for effect in outcome.relationship_effects].count("qa_01") == 1
    direct = next(effect for effect in outcome.relationship_effects if effect.source_id == "qa_01")
    witness = next(effect for effect in outcome.relationship_effects if effect.source_id == "frontend_01")
    assert "direct" in direct.reason_codes
    assert abs(witness.trust_delta) < abs(direct.trust_delta)


def test_apology_recovery_is_bounded_and_does_not_create_severe_events() -> None:
    outcome = RelationshipPolicyEngine().evaluate(
        make_impact("apology", 2, observable=False),
        actor_id="player",
        direct_target_ids=["qa_01"],
        turn=2,
    )

    effect = outcome.relationship_effects[0]
    assert 0 < effect.trust_delta <= 25
    assert effect.fear_delta <= 0
    assert outcome.mandatory_world_events == []

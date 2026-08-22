from __future__ import annotations

from collections.abc import Iterable

from app.game.social_rules import (
    BASE_RELATIONSHIP_IMPACTS,
    HARMFUL_ACTION_FAMILIES,
    ROLE_FACTORS,
    SEVERE_ACTION_FAMILIES,
)
from app.models import (
    EmotionEffect,
    Memory,
    MemoryEffect,
    PolicyModifier,
    RelationshipEffect,
    SocialImpactClassification,
    SocialPolicyOutcome,
    WorldEvent,
)


class RelationshipPolicyEngine:
    """Server-owned deterministic policy for social relationship consequences."""

    def evaluate(
        self,
        classification: SocialImpactClassification,
        *,
        actor_id: str,
        direct_target_ids: Iterable[str],
        affected_target_ids: Iterable[str] = (),
        object_owner_id: str | None = None,
        witness_ids: Iterable[str] = (),
        repeated: bool = False,
        power_abuse: bool = False,
        turn: int,
    ) -> SocialPolicyOutcome:
        family = classification.action_family
        base = BASE_RELATIONSHIP_IMPACTS[family]
        harmful = family in HARMFUL_ACTION_FAMILIES
        modifiers = self._modifiers(classification, harmful, repeated, power_abuse)
        multiplier = 1.0
        for modifier in modifiers:
            multiplier *= modifier.multiplier

        role_by_npc: dict[str, str] = {}
        self._assign_role(role_by_npc, direct_target_ids, "direct")
        if object_owner_id:
            self._assign_role(role_by_npc, [object_owner_id], "owner")
        self._assign_role(role_by_npc, affected_target_ids, "affected")
        self._assign_role(role_by_npc, witness_ids, "witness")

        effects: list[RelationshipEffect] = []
        emotions: list[EmotionEffect] = []
        memories: list[MemoryEffect] = []
        for npc_id, role in role_by_npc.items():
            factor = ROLE_FACTORS[role]
            effect_multiplier = multiplier * factor
            effects.append(
                RelationshipEffect(
                    source_id=npc_id,
                    target_id=actor_id,
                    trust_delta=self._scaled(base.trust, effect_multiplier, classification.severity),
                    tension_delta=self._scaled(base.tension, effect_multiplier, classification.severity),
                    respect_delta=self._scaled(base.respect, effect_multiplier, classification.severity),
                    fear_delta=self._scaled(base.fear, effect_multiplier, classification.severity),
                    grievance_delta=self._scaled(base.grievance, effect_multiplier, classification.severity),
                    reason_codes=[role, *classification.reason_codes],
                )
            )
            emotion, stress_delta, cooperation_delta = self._emotion_effect(
                family,
                classification.severity,
                factor,
                role,
            )
            emotions.append(
                EmotionEffect(
                    npc_id=npc_id,
                    emotion=emotion,
                    stress_delta=stress_delta,
                    cooperation_delta=cooperation_delta,
                )
            )
            if harmful and (classification.severity >= 3 or repeated):
                memories.append(
                    MemoryEffect(
                        npc_id=npc_id,
                        memory=Memory(
                            summary=f"Player performed {family} affecting {role} participant {npc_id}.",
                            importance=min(1.0, 0.55 + classification.severity * 0.09),
                            turn=turn,
                        ),
                    )
                )

        return SocialPolicyOutcome(
            conduct_level=base.conduct_level,  # type: ignore[arg-type]
            relationship_effects=effects,
            emotion_effects=emotions,
            mandatory_world_events=self._mandatory_events(classification),
            memory_effects=memories,
            applied_modifiers=modifiers,
        )

    def _assign_role(self, roles: dict[str, str], npc_ids: Iterable[str], role: str) -> None:
        priority = {"witness": 1, "affected": 2, "owner": 3, "direct": 4}
        for npc_id in npc_ids:
            current = roles.get(npc_id)
            if current is None or priority[role] > priority[current]:
                roles[npc_id] = role

    def _modifiers(
        self,
        classification: SocialImpactClassification,
        harmful: bool,
        repeated: bool,
        power_abuse: bool,
    ) -> list[PolicyModifier]:
        if not harmful:
            return []
        modifiers: list[PolicyModifier] = []
        if classification.observable:
            modifiers.append(PolicyModifier(code="public_or_observed", multiplier=1.2))
        if classification.intentionality == "deliberate":
            modifiers.append(PolicyModifier(code="deliberate", multiplier=1.2))
        elif classification.intentionality == "reckless":
            modifiers.append(PolicyModifier(code="reckless", multiplier=1.1))
        if repeated:
            modifiers.append(PolicyModifier(code="repeated_action", multiplier=1.3))
        if power_abuse:
            modifiers.append(PolicyModifier(code="power_abuse", multiplier=1.2))
        return modifiers

    def _scaled(self, value: int, multiplier: float, severity: int) -> int:
        severity_factor = 0.8 + severity * 0.1
        limit = 60 if severity >= 4 else 25
        return max(-limit, min(limit, round(value * multiplier * severity_factor)))

    def _emotion_effect(self, family: str, severity: int, factor: float, role: str) -> tuple[str, int, int]:
        if family == "property_interference":
            emotion = "guarded" if role == "witness" else "angry" if severity >= 3 else "guarded"
            return emotion, round(severity * 6 * factor), round(-severity * 5 * factor)
        if family == "property_aggression":
            emotion = "shocked" if role == "witness" else "angry"
            return emotion, round(severity * 8 * factor), round(-severity * 7 * factor)
        if family in {"physical_intimidation", "physical_assault", "threat"}:
            emotion = "shocked" if role == "witness" else "afraid" if severity >= 4 else "shocked"
            return emotion, round(severity * 8 * factor), round(-severity * 7 * factor)
        if family in HARMFUL_ACTION_FAMILIES:
            emotion = "guarded" if role == "witness" else "angry" if severity >= 3 else "guarded"
            return emotion, round(severity * 6 * factor), round(-severity * 5 * factor)
        if family in {"apology", "repair_action", "mediation"}:
            emotion = "attentive" if role == "witness" else "cautiously_relieved"
            return emotion, round(-severity * 2 * factor), round(severity * 2 * factor)
        if family == "support":
            emotion = "attentive" if role == "witness" else "supported"
            return emotion, round(-severity * 3 * factor), round(severity * 3 * factor)
        return "attentive", round(-factor), round(factor)

    def _mandatory_events(self, classification: SocialImpactClassification) -> list[WorldEvent]:
        family = classification.action_family
        events: list[WorldEvent] = []
        if family == "property_aggression" and classification.object_id:
            events.append(
                WorldEvent(
                    event_type="object_damaged",
                    target_id=classification.object_id,
                    detail="공격적인 행동으로 관련 사무용품이 손상됐습니다.",
                )
            )
        if family == "physical_assault":
            events.extend(
                [
                    WorldEvent(event_type="security_called", detail="신체 공격으로 Security가 호출됐습니다."),
                    WorldEvent(event_type="dialogue_refused", detail="직접 피해자가 정상적인 대화를 거부합니다."),
                ]
            )
        elif family in SEVERE_ACTION_FAMILIES and classification.severity >= 4:
            events.append(WorldEvent(event_type="hr_escalated", detail="갈등 사건이 HR에 보고됐습니다."))
        if family == "public_humiliation" and classification.severity >= 4:
            events.append(WorldEvent(event_type="meeting_interrupted", detail="공개적인 갈등으로 회의가 중단됐습니다."))
        return events

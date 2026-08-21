from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ActionType = Literal[
    "talk",
    "ask",
    "accuse",
    "defend",
    "order",
    "inspect",
    "show_evidence",
    "request_evidence",
    "move",
    "summon_meeting",
    "report_conclusion",
    "social_action",
]

SocialActionFamily = Literal[
    "constructive_dialogue",
    "evidence_based_confrontation",
    "verbal_pressure",
    "insult",
    "public_humiliation",
    "threat",
    "property_interference",
    "property_aggression",
    "physical_intimidation",
    "physical_assault",
    "sabotage",
    "deception",
    "support",
    "apology",
    "mediation",
    "repair_action",
]

SocialReasonCode = Literal[
    "constructive",
    "factual_challenge",
    "coercion",
    "personal_attack",
    "public_exposure",
    "credible_threat",
    "property_violation",
    "property_damage",
    "physical_danger",
    "work_disruption",
    "dishonesty",
    "support",
    "accountability",
    "repair",
    "mediation",
    "repeat_behavior",
    "power_abuse",
]


class FactDefinition(BaseModel):
    id: str
    statement: str
    category: Literal["canonical", "evidence", "world_event"]
    source_evidence_ids: list[str] = Field(default_factory=list)
    revealable: bool = True


class Personality(BaseModel):
    assertiveness: int = Field(ge=0, le=100)
    cooperativeness: int = Field(ge=0, le=100)
    risk_aversion: int = Field(ge=0, le=100)
    blame_sensitivity: int = Field(ge=0, le=100)


class DynamicState(BaseModel):
    emotion: str = "neutral"
    stress: int = Field(default=20, ge=0, le=100)
    trust_toward_player: int = Field(default=0, ge=-100, le=100)
    cooperation: int = Field(default=60, ge=0, le=100)


class Belief(BaseModel):
    subject: str
    belief: str
    confidence: float = Field(default=0.5, ge=0, le=1)


class Memory(BaseModel):
    summary: str
    importance: float = Field(default=0.5, ge=0, le=1)
    turn: int


class Relationship(BaseModel):
    target_npc_id: str
    trust: int = Field(default=0, ge=-100, le=100)
    tension: int = Field(default=0, ge=0, le=100)


class RelationshipUpdate(BaseModel):
    target_npc_id: str
    trust_delta: int = Field(default=0, ge=-100, le=100)
    tension_delta: int = Field(default=0, ge=-100, le=100)


class RelationshipState(BaseModel):
    source_id: str
    target_id: str
    trust: int = Field(default=0, ge=-100, le=100)
    tension: int = Field(default=0, ge=0, le=100)
    respect: int = Field(default=0, ge=-100, le=100)
    fear: int = Field(default=0, ge=0, le=100)
    grievance: int = Field(default=0, ge=0, le=100)
    repair_stage: Literal["none", "acknowledged", "apologized", "repaired", "mediated"] = "none"
    trust_ceiling: int | None = Field(default=None, ge=-100, le=100)
    fear_floor: int = Field(default=0, ge=0, le=100)
    last_changed_turn: int = 0


class WorldObjectDefinition(BaseModel):
    id: str
    name: str
    owner_id: str | None = None
    location: Literal["meeting_room", "dev_area", "qa_desk", "pm_desk"]
    portable: bool = True
    destructible: bool = True


class WorldObjectState(WorldObjectDefinition):
    holder_id: str | None = None
    condition: Literal["normal", "damaged", "destroyed"] = "normal"


class SocialImpactClassification(BaseModel):
    action_family: SocialActionFamily
    direct_target_ids: list[str] = Field(default_factory=list)
    affected_target_ids: list[str] = Field(default_factory=list)
    object_id: str | None = None
    severity: int = Field(ge=1, le=5)
    intentionality: Literal["accidental", "reckless", "deliberate"]
    observable: bool
    evidence_based: bool
    reason_codes: list[SocialReasonCode] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class RelationshipEffect(BaseModel):
    source_id: str
    target_id: str
    trust_delta: int = Field(ge=-100, le=100)
    tension_delta: int = Field(ge=-100, le=100)
    respect_delta: int = Field(ge=-100, le=100)
    fear_delta: int = Field(ge=-100, le=100)
    grievance_delta: int = Field(ge=-100, le=100)
    reason_codes: list[str] = Field(default_factory=list)


class EmotionEffect(BaseModel):
    npc_id: str
    emotion: str
    stress_delta: int = Field(ge=-100, le=100)
    cooperation_delta: int = Field(ge=-100, le=100)


class PolicyModifier(BaseModel):
    code: str
    multiplier: float = Field(ge=0, le=3)


class WorldEvent(BaseModel):
    event_type: str
    target_id: str | None = None
    detail: str


class MemoryEffect(BaseModel):
    npc_id: str
    memory: Memory


class SocialPolicyOutcome(BaseModel):
    conduct_level: Literal["permitted", "inappropriate", "misconduct", "severe_misconduct"]
    relationship_effects: list[RelationshipEffect] = Field(default_factory=list)
    emotion_effects: list[EmotionEffect] = Field(default_factory=list)
    mandatory_world_events: list[WorldEvent] = Field(default_factory=list)
    memory_effects: list[MemoryEffect] = Field(default_factory=list)
    applied_modifiers: list[PolicyModifier] = Field(default_factory=list)


class NPCState(BaseModel):
    id: str
    name: str
    role: str
    personality: Personality
    dynamic_state: DynamicState
    known_fact_ids: list[str] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    beliefs: list[Belief] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    recent_memories: list[Memory] = Field(default_factory=list)
    important_memories: list[Memory] = Field(default_factory=list)


class Evidence(BaseModel):
    id: str
    title: str
    summary: str
    content: str = ""
    source_npc_id: str | None = None
    discovered: bool = False


class EventLogEntry(BaseModel):
    id: int
    turn: int
    actor: str
    actor_id: str | None = None
    message: str
    event_type: str = "dialogue"
    created_at: datetime


class GuardrailCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class AgentDecision(BaseModel):
    npc_id: str
    emotion: str
    stress_delta: int
    trust_delta: int
    cooperation_delta: int
    belief_updates: list[Belief] = Field(default_factory=list)
    relationship_updates: list[RelationshipUpdate] = Field(default_factory=list)
    grounding_type: Literal["fact", "belief", "acknowledgement"] = "fact"
    knowledge_refs: list[str] = Field(default_factory=list)
    memory_candidate: Memory | None = None
    action_type: str
    action_target: str | None = None
    dialogue: str


class IntentClassification(BaseModel):
    intent: ActionType
    target_npc_id: str | None = None
    evidence_id: str | None = None
    location: Literal["meeting_room", "dev_area", "qa_desk", "pm_desk"] | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class AgentTrace(BaseModel):
    id: int
    turn: int
    event: str
    npc_id: str
    provider: Literal["cli", "openai", "deterministic-mock"] = "deterministic-mock"
    context_summary: str
    known_fact_ids: list[str] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    retrieved_rules: list[str] = Field(default_factory=list)
    decision: AgentDecision
    requested_decision: AgentDecision | None = None
    guardrails: list[GuardrailCheck] = Field(default_factory=list)
    fallback_used: bool = False


class SocialEventTrace(BaseModel):
    id: int
    turn: int
    actor_id: str = "player"
    provider: Literal["cli", "openai", "deterministic-mock"]
    player_input: str
    classification: SocialImpactClassification
    requested_classification: SocialImpactClassification | None = None
    policy_outcome: SocialPolicyOutcome
    guardrails: list[GuardrailCheck] = Field(default_factory=list)
    fallback_used: bool = False


class FallbackNotice(BaseModel):
    id: int
    turn: int
    stage: Literal[
        "intent_provider",
        "intent_guardrail",
        "decision_provider",
        "decision_guardrail",
        "social_impact_provider",
        "social_impact_guardrail",
    ]
    provider: Literal["cli", "openai", "deterministic-mock"]
    reason: str
    created_at: datetime


class GameSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str
    turn: int
    current_location: str
    incident_status: str
    ai_provider: Literal["cli", "openai", "deterministic-mock"]
    ai_model: str
    objective: list[str]
    npcs: list[NPCState]
    relationships: list[RelationshipState]
    world_objects: list[WorldObjectState]
    social_events: list[SocialEventTrace]
    dialogue_refused_npc_ids: list[str]
    evidences: list[Evidence]
    events: list[EventLogEntry]
    agent_traces: list[AgentTrace]
    fallback_notices: list[FallbackNotice]
    available_actions: list[str]
    completed: bool = False
    result: "GameResult | None" = None


class ActionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    intent_hint: IntentClassification | None = None
    target_hint: str | None = None


class ActionResponse(BaseModel):
    snapshot: GameSnapshot
    classified_action: str
    message: str
    intent_provider: Literal["cli", "openai", "deterministic-mock", "ui"]
    intent_confidence: float = Field(ge=0, le=1)
    intent_fallback_used: bool = False
    social_impact_provider: Literal["cli", "openai", "deterministic-mock"] | None = None
    social_impact_fallback_used: bool = False


class IncidentReportRequest(BaseModel):
    primary_cause: str = Field(min_length=1, max_length=500)
    contributing_factors: list[str] = Field(default_factory=list, max_length=10)


class GameResult(BaseModel):
    incident_diagnosis: int = Field(ge=0, le=100)
    evidence_coverage: int = Field(ge=0, le=100)
    team_trust: int = Field(ge=0, le=100)
    recovery_efficiency: int = Field(ge=0, le=100)
    summary: str


GameSnapshot.model_rebuild()

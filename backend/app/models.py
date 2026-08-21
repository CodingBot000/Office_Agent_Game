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
]


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


class NPCState(BaseModel):
    id: str
    name: str
    role: str
    personality: Personality
    dynamic_state: DynamicState
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
    known_facts: list[str] = Field(default_factory=list)
    retrieved_rules: list[str] = Field(default_factory=list)
    decision: AgentDecision
    guardrails: list[GuardrailCheck] = Field(default_factory=list)
    fallback_used: bool = False


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
    evidences: list[Evidence]
    events: list[EventLogEntry]
    agent_traces: list[AgentTrace]
    available_actions: list[str]
    completed: bool = False
    result: "GameResult | None" = None


class ActionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ActionResponse(BaseModel):
    snapshot: GameSnapshot
    classified_action: str
    message: str
    intent_provider: Literal["cli", "openai", "deterministic-mock"]
    intent_confidence: float = Field(ge=0, le=1)
    intent_fallback_used: bool = False


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

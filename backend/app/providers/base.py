from dataclasses import dataclass
from typing import Protocol

from app.models import AgentDecision, Evidence, IntentClassification, NPCState, SocialImpactClassification


class ProviderError(RuntimeError):
    """Raised when a provider cannot return a validated decision."""


@dataclass(frozen=True)
class DecisionContext:
    mode: str
    player_input: str
    turn: int
    npc: NPCState
    target_npc_id: str
    available_facts: tuple[str, ...]
    available_evidence_ids: tuple[str, ...]
    recent_events: tuple[str, ...]
    incident_rules: tuple[str, ...]
    question_type: str = "none"
    reference_scope: str = "none"
    discovered_evidence_ids: tuple[str, ...] = ()
    referenced_evidence_id: str | None = None
    referenced_evidence_title: str | None = None
    referenced_evidence_summary: str | None = None
    referenced_evidence_content: str | None = None
    responsibility_map: tuple[str, ...] = ()
    visible_evidences: tuple[Evidence, ...] = ()
    available_npcs: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentContext:
    player_input: str
    current_location: str
    target_hint: str | None
    available_npcs: tuple[str, ...]
    available_npc_ids: tuple[str, ...]
    available_evidence_ids: tuple[str, ...]
    discovered_evidence_ids: tuple[str, ...]
    available_locations: tuple[str, ...]
    available_actions: tuple[str, ...]
    available_evidences: tuple[str, ...] = ()
    recent_events: tuple[str, ...] = ()
    latest_discovered_evidence_id: str | None = None


@dataclass(frozen=True)
class SocialImpactContext:
    player_input: str
    current_location: str
    target_hint: str | None
    available_npcs: tuple[str, ...]
    available_npc_ids: tuple[str, ...]
    available_objects: tuple[str, ...]
    available_object_ids: tuple[str, ...]
    recent_social_events: tuple[str, ...]


class AgentProvider(Protocol):
    name: str
    model: str

    def decide(self, context: DecisionContext) -> AgentDecision:
        """Return one schema-validated NPC decision candidate."""


class IntentProvider(Protocol):
    name: str
    model: str

    def classify(self, context: IntentContext) -> IntentClassification:
        """Return one schema-validated player intent candidate."""


class SocialImpactProvider(Protocol):
    name: str
    model: str

    def classify_social_impact(self, context: SocialImpactContext) -> SocialImpactClassification:
        """Return one schema-validated social impact candidate without relationship deltas."""

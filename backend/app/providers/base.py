from dataclasses import dataclass
from typing import Protocol

from app.models import AgentDecision, IntentClassification, NPCState


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
    incident_rules: tuple[str, ...]


@dataclass(frozen=True)
class IntentContext:
    player_input: str
    current_location: str
    target_hint: str | None
    available_npcs: tuple[str, ...]
    available_npc_ids: tuple[str, ...]
    available_evidence_ids: tuple[str, ...]
    available_locations: tuple[str, ...]
    available_actions: tuple[str, ...]


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

from dataclasses import dataclass
from typing import Protocol

from app.models import AgentDecision, NPCState


class ProviderError(RuntimeError):
    """Raised when a provider cannot return a validated decision."""


@dataclass(frozen=True)
class DecisionContext:
    mode: str
    player_input: str
    turn: int
    npc: NPCState
    target_npc_id: str
    available_evidence_ids: tuple[str, ...]
    incident_rules: tuple[str, ...]


class AgentProvider(Protocol):
    name: str
    model: str

    def decide(self, context: DecisionContext) -> AgentDecision:
        """Return one schema-validated NPC decision candidate."""

"""Shared state invariants for dialogue and rule-driven game actions."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.game.seed import relationship_key
from app.models import NPCState, Relationship, RelationshipState

if TYPE_CHECKING:
    from app.game.session import GameSession


def npc_response_block(npc: NPCState, refused_ids: set[str]) -> str | None:
    if npc.physical_state == "comatose":
        return f"{npc.name}은(는) 혼수상태로 답변할 수 없습니다."
    if npc.id in refused_ids:
        return "심각한 갈등 사건이 해결되지 않아 현재 정상적인 대화를 거부합니다. 사과, 피해 복구, 중재가 필요합니다."
    return None


def change_relationship(
    session: GameSession,
    source_id: str,
    target_id: str,
    *,
    trust_delta: int = 0,
    tension_delta: int = 0,
    respect_delta: int = 0,
    fear_delta: int = 0,
    grievance_delta: int = 0,
    policy_updates: dict[str, object] | None = None,
) -> RelationshipState:
    """The graph is authoritative; legacy NPC fields are projections of it."""
    edge_id = relationship_key(source_id, target_id)
    edge = session.relationships[edge_id]
    values = {**edge.model_dump(), **(policy_updates or {})}
    ceiling = values["trust_ceiling"]
    values.update(
        trust=min(max(-100, edge.trust + trust_delta), 100 if ceiling is None else ceiling),
        tension=max(0, min(100, edge.tension + tension_delta)),
        respect=max(-100, min(100, edge.respect + respect_delta)),
        fear=max(values["fear_floor"], min(100, edge.fear + fear_delta)),
        grievance=max(0, min(100, edge.grievance + grievance_delta)),
        last_changed_turn=session.turn,
    )
    updated = RelationshipState.model_validate(values)
    session.relationships[edge_id] = updated
    npc = session.npcs.get(source_id)
    if npc is not None:
        if target_id == "player":
            npc.dynamic_state = npc.dynamic_state.model_copy(update={"trust_toward_player": updated.trust})
        else:
            npc.relationships = [item for item in npc.relationships if item.target_npc_id != target_id]
            npc.relationships.append(Relationship(target_npc_id=target_id, trust=updated.trust, tension=updated.tension))
    return updated

from __future__ import annotations

from typing import TYPE_CHECKING

from app.game.seed import NPC_HOME_LOCATIONS
from app.models import AvailableGameAction, PlayerInventory

if TYPE_CHECKING:
    from app.game.engine import GameSession


def build_player_inventory(session: GameSession) -> PlayerInventory:
    return PlayerInventory(
        held_object_ids=sorted(
            object_id
            for object_id, world_object in session.world_objects.items()
            if world_object.holder_id == "player"
        ),
        max_held_objects=1,
    )


def _npc_ids_at_current_location(session: GameSession) -> list[str]:
    if session.current_location == "meeting_room":
        return list(session.npcs)
    return [
        npc_id
        for npc_id in session.npcs
        if NPC_HOME_LOCATIONS.get(npc_id) == session.current_location
    ]


def build_available_game_actions(session: GameSession) -> list[AvailableGameAction]:
    inventory = build_player_inventory(session)
    actions: list[AvailableGameAction] = []
    for world_object in sorted(session.world_objects.values(), key=lambda item: item.id):
        if world_object.holder_id == "player":
            if world_object.destructible and world_object.condition != "destroyed":
                actions.append(
                    AvailableGameAction(
                        id=f"break_{world_object.id}",
                        family="break_held_object",
                        label=f"Break {world_object.name}",
                        object_id=world_object.id,
                        owner_id=world_object.owner_id,
                        scope="held_item",
                        location=session.current_location,
                    )
                )
            actions.append(
                AvailableGameAction(
                    id=f"drop_{world_object.id}",
                    family="drop_held_object",
                    label=f"Drop {world_object.name}",
                    object_id=world_object.id,
                    owner_id=world_object.owner_id,
                    scope="held_item",
                    location=session.current_location,
                )
            )

            for npc_id in _npc_ids_at_current_location(session):
                npc = session.npcs[npc_id]
                if npc.is_fallen:
                    continue
                actions.append(
                    AvailableGameAction(
                        id=f"throw_{world_object.id}_at_{npc_id}",
                        family="throw_held_object",
                        label=f"Throw {world_object.name} at {npc.name}",
                        object_id=world_object.id,
                        target_id=npc_id,
                        owner_id=world_object.owner_id,
                        scope="target",
                        location=session.current_location,
                    )
                )
            continue

        if world_object.location != session.current_location:
            continue

        if world_object.evidence_id and world_object.condition != "destroyed":
            actions.append(
                AvailableGameAction(
                    id=f"inspect_{world_object.id}",
                    family="inspect_object",
                    label=f"Inspect {world_object.name}",
                    object_id=world_object.id,
                    target_id=world_object.owner_id,
                    owner_id=world_object.owner_id,
                    scope="world",
                    location=session.current_location,
                )
            )

        if world_object.portable and world_object.condition == "normal":
            can_pick_up = len(inventory.held_object_ids) < inventory.max_held_objects
            actions.append(
                AvailableGameAction(
                    id=f"pick_up_{world_object.id}",
                    family="pick_up_object",
                    label=f"Pick up {world_object.name}",
                    object_id=world_object.id,
                    target_id=world_object.owner_id,
                    owner_id=world_object.owner_id,
                    scope="world",
                    location=session.current_location,
                    enabled=can_pick_up,
                    disabled_reason=None if can_pick_up else "Player is already holding an object.",
                )
            )

    return actions

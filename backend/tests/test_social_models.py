from app.game.seed import (
    NPC_HOME_LOCATIONS,
    WORLD_OBJECT_DEFINITIONS,
    WORLD_OBJECT_REGISTRY,
    build_initial_npcs,
    build_relationship_graph,
)


def test_world_object_registry_has_valid_unique_objects() -> None:
    npcs = build_initial_npcs()

    assert len(WORLD_OBJECT_REGISTRY) == len(WORLD_OBJECT_DEFINITIONS)
    assert all(item.id and item.name for item in WORLD_OBJECT_DEFINITIONS)
    assert all(item.owner_id is None or item.owner_id in npcs for item in WORLD_OBJECT_DEFINITIONS)
    assert all(item.location in {"meeting_room", *NPC_HOME_LOCATIONS.values()} for item in WORLD_OBJECT_DEFINITIONS)


def test_relationship_graph_contains_all_directional_entity_edges() -> None:
    npcs = build_initial_npcs()
    graph = build_relationship_graph(npcs)
    entity_count = len(npcs) + 1

    assert len(graph) == entity_count * (entity_count - 1)
    assert graph["qa_01->player"].trust == npcs["qa_01"].dynamic_state.trust_toward_player
    assert graph["qa_01->backend_01"].tension == 60
    assert graph["player->qa_01"].source_id == "player"

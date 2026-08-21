from app.game.engine import GameEngine
from app.models import GameActionRequest, IntentClassification


def move(engine: GameEngine, session_id: str, location: str) -> None:
    engine.submit_action(
        session_id,
        f"{location}으로 이동한다.",
        IntentClassification(intent="move", location=location, confidence=1.0),  # type: ignore[arg-type]
    )


def action_ids(snapshot) -> set[str]:
    return {action.id for action in snapshot.available_game_actions if action.enabled}


def test_available_game_actions_follow_location() -> None:
    engine = GameEngine()
    started = engine.create_session()
    assert "pick_up_backend_keyboard" not in action_ids(started)

    move(engine, started.session_id, "dev_area")
    dev = engine.get_session(started.session_id)
    assert {"pick_up_backend_keyboard", "pick_up_frontend_keyboard"}.issubset(action_ids(engine.snapshot(dev)))
    assert "pick_up_pm_keyboard" not in action_ids(engine.snapshot(dev))

    move(engine, started.session_id, "pm_desk")
    pm = engine.get_session(started.session_id)
    pm_actions = action_ids(engine.snapshot(pm))
    assert "pick_up_pm_keyboard" in pm_actions
    assert "pick_up_backend_keyboard" not in pm_actions

    move(engine, started.session_id, "dev_area")
    dev_again = engine.get_session(started.session_id)
    assert "pick_up_backend_keyboard" in action_ids(engine.snapshot(dev_again))


def test_pickup_and_break_updates_holder_owner_relationship_and_memory() -> None:
    engine = GameEngine()
    started = engine.create_session()
    move(engine, started.session_id, "dev_area")

    picked = engine.submit_game_action(started.session_id, GameActionRequest(action_id="pick_up_backend_keyboard"))
    backend_after_pickup = next(npc for npc in picked.snapshot.npcs if npc.id == "backend_01")
    keyboard_after_pickup = next(item for item in picked.snapshot.world_objects if item.id == "backend_keyboard")
    assert picked.blocked is False
    assert keyboard_after_pickup.holder_id == "player"
    assert "backend_keyboard" in picked.snapshot.player_inventory.held_object_ids
    assert backend_after_pickup.dynamic_state.emotion in {"angry", "guarded"}
    assert "break_backend_keyboard" in action_ids(picked.snapshot)
    assert "pick_up_frontend_keyboard" not in action_ids(picked.snapshot)

    broken = engine.submit_game_action(started.session_id, GameActionRequest(action_id="break_backend_keyboard"))
    backend_after_break = next(npc for npc in broken.snapshot.npcs if npc.id == "backend_01")
    keyboard_after_break = next(item for item in broken.snapshot.world_objects if item.id == "backend_keyboard")
    relation = next(
        edge for edge in broken.snapshot.relationships if edge.source_id == "backend_01" and edge.target_id == "player"
    )
    assert broken.blocked is False
    assert keyboard_after_break.holder_id is None
    assert keyboard_after_break.condition == "destroyed"
    assert broken.snapshot.player_inventory.held_object_ids == []
    assert relation.grievance > 0
    assert relation.trust_ceiling == 20
    assert backend_after_break.important_memories
    assert broken.snapshot.incident_status == "HR_ESCALATED"
    assert broken.snapshot.game_action_traces[-1].condition_after == "destroyed"


def test_natural_language_game_action_is_blocked_without_progress() -> None:
    engine = GameEngine()
    started = engine.create_session()

    response = engine.submit_action(started.session_id, "키보드를 부순다.", target_hint="backend_01")

    assert response.blocked is True
    assert response.alert == "Use the provided action buttons to perform game actions."
    assert response.snapshot.turn == 0
    assert response.snapshot.events[-1].actor == "System"
    assert response.snapshot.game_action_traces == []


def test_invalid_game_action_does_not_mutate_world_state() -> None:
    engine = GameEngine()
    started = engine.create_session()

    response = engine.submit_game_action(started.session_id, GameActionRequest(action_id="break_backend_keyboard"))

    assert response.blocked is True
    assert response.snapshot.turn == 0
    assert response.snapshot.game_action_traces[-1].blocked is True
    keyboard = next(item for item in response.snapshot.world_objects if item.id == "backend_keyboard")
    assert keyboard.condition == "normal"


def test_inspect_button_discovers_linked_evidence() -> None:
    engine = GameEngine()
    started = engine.create_session()
    move(engine, started.session_id, "qa_desk")
    qa_session = engine.get_session(started.session_id)
    assert "inspect_qa_warning_printout" in action_ids(engine.snapshot(qa_session))

    response = engine.submit_game_action(
        started.session_id,
        GameActionRequest(action_id="inspect_qa_warning_printout"),
    )

    warning = next(item for item in response.snapshot.evidences if item.id == "qa_warning_message")
    assert warning.discovered is True
    assert response.snapshot.game_action_traces[-1].family == "inspect_object"

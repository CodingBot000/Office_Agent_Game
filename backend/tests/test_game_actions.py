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
    frontend_after_pickup = next(npc for npc in picked.snapshot.npcs if npc.id == "frontend_01")
    keyboard_after_pickup = next(item for item in picked.snapshot.world_objects if item.id == "backend_keyboard")
    assert picked.blocked is False
    assert keyboard_after_pickup.holder_id == "player"
    assert "backend_keyboard" in picked.snapshot.player_inventory.held_object_ids
    assert backend_after_pickup.dynamic_state.emotion == "angry"
    assert frontend_after_pickup.dynamic_state.emotion == "guarded"
    assert frontend_after_pickup.dynamic_state.stress < backend_after_pickup.dynamic_state.stress
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


def test_held_item_actions_remain_available_and_throw_targets_all_active_npcs() -> None:
    engine = GameEngine()
    started = engine.create_session()
    move(engine, started.session_id, "dev_area")
    engine.submit_game_action(started.session_id, GameActionRequest(action_id="pick_up_backend_keyboard"))

    move(engine, started.session_id, "pm_desk")
    pm_snapshot = engine.snapshot(engine.get_session(started.session_id))
    actions = {action.id: action for action in pm_snapshot.available_game_actions}

    assert actions["break_backend_keyboard"].scope == "held_item"
    assert actions["break_backend_keyboard"].target_id is None
    assert actions["break_backend_keyboard"].owner_id == "backend_01"
    assert actions["drop_backend_keyboard"].scope == "held_item"
    assert actions["drop_backend_keyboard"].target_id is None

    throw = actions["throw_backend_keyboard_at_pm_01"]
    assert throw.family == "throw_held_object"
    assert throw.scope == "target"
    assert throw.target_id == "pm_01"
    assert throw.owner_id == "backend_01"
    assert {
        "throw_backend_keyboard_at_backend_01",
        "throw_backend_keyboard_at_frontend_01",
        "throw_backend_keyboard_at_qa_01",
        "throw_backend_keyboard_at_pm_01",
    }.issubset(actions)


def test_remote_throw_uses_target_location_for_witness_policy() -> None:
    engine = GameEngine()
    started = engine.create_session()
    move(engine, started.session_id, "dev_area")
    engine.submit_game_action(started.session_id, GameActionRequest(action_id="pick_up_backend_keyboard"))
    before_throw = engine.snapshot(engine.get_session(started.session_id))
    pm_stress_before = next(npc for npc in before_throw.npcs if npc.id == "pm_01").dynamic_state.stress

    thrown = engine.submit_game_action(
        started.session_id,
        GameActionRequest(action_id="throw_backend_keyboard_at_qa_01"),
    )

    qa = next(npc for npc in thrown.snapshot.npcs if npc.id == "qa_01")
    pm = next(npc for npc in thrown.snapshot.npcs if npc.id == "pm_01")
    assert qa.is_fallen is True
    assert qa.dynamic_state.emotion == "afraid"
    assert pm.dynamic_state.stress == pm_stress_before


def test_drop_marks_held_item_as_dropped_at_current_location() -> None:
    engine = GameEngine()
    started = engine.create_session()
    move(engine, started.session_id, "dev_area")
    engine.submit_game_action(started.session_id, GameActionRequest(action_id="pick_up_backend_keyboard"))

    move(engine, started.session_id, "pm_desk")
    dropped = engine.submit_game_action(started.session_id, GameActionRequest(action_id="drop_backend_keyboard"))
    keyboard = next(item for item in dropped.snapshot.world_objects if item.id == "backend_keyboard")

    assert keyboard.holder_id is None
    assert keyboard.location == "pm_desk"
    assert keyboard.is_dropped is True


def test_throw_held_object_breaks_item_and_fells_target_npc() -> None:
    engine = GameEngine()
    started = engine.create_session()
    move(engine, started.session_id, "dev_area")
    engine.submit_game_action(started.session_id, GameActionRequest(action_id="pick_up_backend_keyboard"))

    thrown = engine.submit_game_action(
        started.session_id,
        GameActionRequest(action_id="throw_backend_keyboard_at_frontend_01"),
    )

    keyboard = next(item for item in thrown.snapshot.world_objects if item.id == "backend_keyboard")
    frontend = next(npc for npc in thrown.snapshot.npcs if npc.id == "frontend_01")
    backend = next(npc for npc in thrown.snapshot.npcs if npc.id == "backend_01")

    assert thrown.blocked is False
    assert keyboard.holder_id is None
    assert keyboard.condition == "destroyed"
    assert thrown.snapshot.player_inventory.held_object_ids == []
    assert frontend.is_fallen is True
    assert frontend.id in thrown.snapshot.dialogue_refused_npc_ids
    assert frontend.dynamic_state.emotion == "afraid"
    assert backend.dynamic_state.emotion == "angry"
    assert thrown.snapshot.incident_status == "SECURITY_ESCALATED"
    assert thrown.snapshot.game_action_traces[-1].family == "throw_held_object"
    assert thrown.snapshot.game_action_traces[-1].target_id == "frontend_01"


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

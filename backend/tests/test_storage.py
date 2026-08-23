import pytest

from app.config import Settings
from app.game.engine import GameEngine
from app.storage import SQLiteSessionRepository


def test_sqlite_repository_restores_session_across_engine_instances(tmp_path) -> None:
    settings = Settings(
        ai_provider="deterministic-mock",
        session_storage="sqlite",
        sqlite_path=str(tmp_path / "sessions.db"),
    )
    first_engine = GameEngine(settings=settings)
    started = first_engine.create_session()
    first_engine.submit_action(started.session_id, "QA에게 배포 전 문제를 질문한다.")

    restarted_engine = GameEngine(settings=settings)
    restored = restarted_engine.snapshot(restarted_engine.get_session(started.session_id))

    assert restored.session_id == started.session_id
    assert restored.turn == 1
    assert any(event.actor == "QA Engineer" for event in restored.events)


@pytest.mark.parametrize("legacy_version", [1, 2])
def test_legacy_known_fact_strings_migrate_to_v7_fact_ids(tmp_path, caplog, legacy_version: int) -> None:
    database_path = tmp_path / f"migration-{legacy_version}.db"
    settings = Settings(
        ai_provider="deterministic-mock",
        session_storage="sqlite",
        sqlite_path=str(database_path),
    )
    engine = GameEngine(settings=settings)
    started = engine.create_session()
    repository = SQLiteSessionRepository(str(database_path))
    payload = repository.load(started.session_id)
    assert payload is not None
    if legacy_version == 1:
        payload.pop("schema_version", None)
    else:
        payload["schema_version"] = legacy_version
    qa = payload["npcs"]["qa_01"]
    qa.pop("known_fact_ids", None)
    qa["known_facts"] = [
        "I found a critical issue before deployment.",
        "I sent a warning message.",
        "I do not have deployment permission.",
        "Legacy fact without a mapping.",
    ]
    repository.save(started.session_id, payload)

    restored_engine = GameEngine(settings=settings)
    restored = restored_engine.get_session(started.session_id)
    migrated_payload = repository.load(started.session_id)

    assert restored.npcs["qa_01"].known_fact_ids == [
        "qa_found_critical_issue",
        "qa_sent_warning",
        "qa_has_no_deploy_permission",
    ]
    assert migrated_payload["schema_version"] == 7
    assert restored.npcs["qa_01"].is_fallen is False
    assert restored.npcs["qa_01"].physical_state == "normal"
    assert "session_migration_unmapped_fact" in caplog.text


def test_v3_relationships_migrate_to_v7_directional_graph(tmp_path) -> None:
    database_path = tmp_path / "relationship-migration.db"
    settings = Settings(
        ai_provider="deterministic-mock",
        session_storage="sqlite",
        sqlite_path=str(database_path),
    )
    engine = GameEngine(settings=settings)
    started = engine.create_session()
    repository = SQLiteSessionRepository(str(database_path))
    payload = repository.load(started.session_id)
    assert payload is not None
    payload["schema_version"] = 3
    payload.pop("relationships", None)
    payload.pop("world_objects", None)
    payload.pop("social_events", None)
    repository.save(started.session_id, payload)

    restored = GameEngine(settings=settings).get_session(started.session_id)
    migrated_payload = repository.load(started.session_id)

    assert migrated_payload is not None
    assert migrated_payload["schema_version"] == 7
    assert restored.relationships["qa_01->player"].trust == 15
    assert restored.relationships["qa_01->backend_01"].tension == 60
    assert restored.relationships["player->qa_01"].trust == 0
    assert restored.world_objects["qa_keyboard"].owner_id == "qa_01"
    assert restored.social_events == []


def test_v4_session_migrates_game_action_inventory_fields_to_v7(tmp_path) -> None:
    database_path = tmp_path / "game-action-migration.db"
    settings = Settings(
        ai_provider="deterministic-mock",
        session_storage="sqlite",
        sqlite_path=str(database_path),
    )
    engine = GameEngine(settings=settings)
    started = engine.create_session()
    repository = SQLiteSessionRepository(str(database_path))
    payload = repository.load(started.session_id)
    assert payload is not None
    payload["schema_version"] = 4
    payload.pop("player_inventory", None)
    payload.pop("game_action_traces", None)
    repository.save(started.session_id, payload)

    restored = GameEngine(settings=settings).get_session(started.session_id)
    migrated_payload = repository.load(started.session_id)

    assert migrated_payload is not None
    assert migrated_payload["schema_version"] == 7
    assert restored.game_action_traces == []
    assert restored.world_objects["backend_keyboard"].holder_id is None
    assert restored.world_objects["backend_keyboard"].condition == "normal"

import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.config import Settings
from app.game.engine import CURRENT_SESSION_SCHEMA_VERSION, GameEngine
from app.storage import MemorySessionRepository, SQLiteSessionRepository


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
def test_legacy_known_fact_strings_migrate_to_v8_fact_ids(tmp_path, caplog, legacy_version: int) -> None:
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
    repository.save(started.session_id, payload, expected_revision=payload["_revision"])

    restored_engine = GameEngine(settings=settings)
    restored = restored_engine.get_session(started.session_id)
    migrated_payload = repository.load(started.session_id)

    assert restored.npcs["qa_01"].known_fact_ids == [
        "qa_found_critical_issue",
        "qa_sent_warning",
        "qa_has_no_deploy_permission",
    ]
    assert migrated_payload["schema_version"] == CURRENT_SESSION_SCHEMA_VERSION
    assert restored.npcs["qa_01"].is_fallen is False
    assert restored.npcs["qa_01"].physical_state == "normal"
    assert "session_migration_unmapped_fact" in caplog.text


def test_v3_relationships_migrate_to_v8_directional_graph(tmp_path) -> None:
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
    repository.save(started.session_id, payload, expected_revision=payload["_revision"])

    restored = GameEngine(settings=settings).get_session(started.session_id)
    migrated_payload = repository.load(started.session_id)

    assert migrated_payload is not None
    assert migrated_payload["schema_version"] == CURRENT_SESSION_SCHEMA_VERSION
    assert restored.relationships["qa_01->player"].trust == 15
    assert restored.relationships["qa_01->backend_01"].tension == 60
    assert restored.relationships["player->qa_01"].trust == 0
    assert restored.world_objects["qa_keyboard"].owner_id == "qa_01"
    assert restored.social_events == []


def test_v4_session_migrates_game_action_inventory_fields_to_v8(tmp_path) -> None:
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
    repository.save(started.session_id, payload, expected_revision=payload["_revision"])

    restored = GameEngine(settings=settings).get_session(started.session_id)
    migrated_payload = repository.load(started.session_id)

    assert migrated_payload is not None
    assert migrated_payload["schema_version"] == CURRENT_SESSION_SCHEMA_VERSION
    assert restored.game_action_traces == []
    assert restored.world_objects["backend_keyboard"].holder_id is None
    assert restored.world_objects["backend_keyboard"].condition == "normal"


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_concurrent_actions_conflict_without_losing_successful_changes(tmp_path, storage):
    from app.models import IntentClassification
    from app.storage import SessionConflictError

    barrier = Barrier(2)

    class ConcurrentIntent:
        name = "deterministic-mock"
        model = "test"

        def classify(self, context):
            barrier.wait(timeout=5)
            return IntentClassification(intent="talk", target_npc_id="qa_01")

    first_repository = MemorySessionRepository() if storage == "memory" else SQLiteSessionRepository(str(tmp_path / "race.db"))
    second_repository = first_repository if storage == "memory" else SQLiteSessionRepository(str(tmp_path / "race.db"))
    engines = [GameEngine(session_repository=repo, intent_provider=ConcurrentIntent()) for repo in (first_repository, second_repository)]
    sid = engines[0].create_session().session_id

    def submit(index):
        try:
            return engines[index].submit_action(sid, f"request {index}")
        except SessionConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sum(response is not None for response in responses) == 1
    restored = engines[0].get_session(sid)
    assert restored.turn == 1
    assert len([event for event in restored.events if event.event_type == "input"]) == 1
    retry_engine = GameEngine(session_repository=first_repository)
    retried = retry_engine.submit_action(sid, "재시도")
    assert retried.snapshot.turn == 2
    assert retried.snapshot.revision == 3


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_reset_prevents_stale_save_and_is_atomic(tmp_path, storage):
    from app.storage import SessionConflictError

    repository = MemorySessionRepository() if storage == "memory" else SQLiteSessionRepository(str(tmp_path / "reset.db"))
    engine = GameEngine(session_repository=repository)
    sid = engine.create_session().session_id
    stale = engine.get_session(sid)
    replacement = engine.reset_session(sid)
    with pytest.raises(SessionConflictError):
        engine._save_session(stale)
    assert repository.load(sid) is None
    assert engine.get_session(replacement.session_id).turn == 0
    existing = repository.load(replacement.session_id)
    with pytest.raises(SessionConflictError):
        repository.replace(replacement.session_id, existing["_revision"] + 1, "not-created", {})
    assert repository.load("not-created") is None
    assert repository.load(replacement.session_id) == existing


def test_sqlite_migrates_legacy_database_revision_once(tmp_path):
    import json
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE game_sessions (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO game_sessions(session_id, payload) VALUES (?, ?)", ("legacy", json.dumps({"turn": 2})))
    repository = SQLiteSessionRepository(str(path))
    assert repository.load("legacy")["_revision"] == 0
    revision = repository.save("legacy", {"turn": 3}, expected_revision=0)
    reopened = SQLiteSessionRepository(str(path))
    assert reopened.load("legacy") == {"turn": 3, "_revision": revision}


@pytest.mark.parametrize("operation", ["reset", "report"])
def test_inflight_action_cannot_overwrite_reset_or_completed_report(operation):
    from threading import Event
    from app.models import IntentClassification, IncidentReportRequest
    from app.storage import SessionConflictError

    entered, release = Event(), Event()

    class WaitingIntent:
        name = "deterministic-mock"
        model = "test"

        def classify(self, context):
            entered.set()
            assert release.wait(timeout=5)
            return IntentClassification(intent="talk", target_npc_id="qa_01")

    engine = GameEngine(intent_provider=WaitingIntent())
    sid = engine.create_session().session_id
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(engine.submit_action, sid, "늦게 끝나는 질문")
        try:
            assert entered.wait(timeout=5)
            result = engine.reset_session(sid) if operation == "reset" else engine.submit_report(sid, IncidentReportRequest(primary_cause="API schema 변경"))
        finally:
            release.set()
        with pytest.raises(SessionConflictError):
            pending.result(timeout=5)
    if operation == "reset":
        assert engine.session_repository.load(sid) is None
        assert engine.get_session(result.session_id).turn == 0
    else:
        assert engine.get_session(sid).completed
        assert all(event.message != "늦게 끝나는 질문" for event in engine.get_session(sid).events)


def test_observed_evidence_and_provenance_survive_sqlite_restart(tmp_path):
    from app.game.evidence_policy import available_fact_ids

    repository = SQLiteSessionRepository(str(tmp_path / "observations.db"))
    engine = GameEngine(session_repository=repository)
    sid = engine.create_session().session_id
    engine.submit_action(sid, "QA 경고 메시지를 보여줘", target_hint="qa_01")
    engine.submit_action(sid, "QA 경고 증거를 제시합니다", target_hint="backend_01")
    restarted = GameEngine(session_repository=SQLiteSessionRepository(str(tmp_path / "observations.db")))
    session = restarted.get_session(sid)
    assert "qa_sent_warning" in available_fact_ids(session, session.npcs["backend_01"])
    assert "team_lead_did_not_confirm_warning" not in available_fact_ids(session, session.npcs["backend_01"])
    assert "qa_warning_message" not in session.npcs["frontend_01"].observed_evidence_ids
    assert restarted._evidence_presentation_count(session, "backend_01", "qa_warning_message") == 1


def test_report_and_extraction_survive_sqlite_restart(tmp_path):
    from app.models import IncidentReportRequest

    path = str(tmp_path / "report.db")
    engine = GameEngine(session_repository=SQLiteSessionRepository(path))
    sid = engine.create_session().session_id
    report = IncidentReportRequest(primary_cause="API 스키마 불일치가 원인입니다", contributing_factors=["일정 압박"])
    result = engine.submit_report(sid, report)
    restored = GameEngine(session_repository=SQLiteSessionRepository(path)).get_session(sid)
    assert restored.report == report
    assert restored.report_extraction.claims
    assert restored.result == result.result


def test_v8_migration_does_not_infer_observations_and_repairs_old_trust_limits():
    repository = MemorySessionRepository()
    engine = GameEngine(session_repository=repository)
    sid = engine.create_session().session_id
    payload = repository.load(sid)
    payload["schema_version"] = 8
    payload["npcs"]["qa_01"].pop("observed_evidence_ids", None)
    payload["relationships"]["qa_01->player"].update(trust=60, trust_ceiling=20, fear_floor=20, fear=0)
    repository.save(sid, payload, expected_revision=payload["_revision"])
    restored = engine.get_session(sid)
    assert restored.npcs["qa_01"].observed_evidence_ids == []
    assert restored.relationships["qa_01->player"].trust == restored.npcs["qa_01"].dynamic_state.trust_toward_player == 20
    assert restored.relationships["qa_01->player"].fear == 20
    assert engine.get_session(sid).revision == restored.revision

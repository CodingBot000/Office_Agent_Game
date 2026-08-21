from app.config import Settings
from app.game.engine import GameEngine


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

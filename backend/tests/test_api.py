from fastapi.testclient import TestClient
import pytest

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_session_action_api() -> None:
    session_response = client.post("/api/v1/sessions")
    assert session_response.status_code == 201
    session_id = session_response.json()["session_id"]

    action_response = client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"text": "QA에게 배포 전 문제를 질문한다."},
    )
    assert action_response.status_code == 200
    assert action_response.json()["classified_action"] == "ask"
    assert action_response.json()["intent_provider"] == "deterministic-mock"
    assert action_response.json()["snapshot"]["turn"] == 1


def test_office_move_hint_api_skips_intent_provider() -> None:
    session_response = client.post("/api/v1/sessions")
    session_id = session_response.json()["session_id"]

    action_response = client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "text": "QA Desk로 이동한다.",
            "intent_hint": {"intent": "move", "location": "qa_desk", "confidence": 1},
        },
    )

    assert action_response.status_code == 200
    assert action_response.json()["intent_provider"] == "ui"
    assert action_response.json()["snapshot"]["current_location"] == "qa_desk"
    events = action_response.json()["snapshot"]["events"]
    assert not any(event["message"] == "QA Desk로 이동한다." for event in events)
    assert events[-1]["message"] == "QA Desk로 이동했습니다."


def test_natural_language_game_action_is_blocked_by_api() -> None:
    session_response = client.post("/api/v1/sessions")
    session_id = session_response.json()["session_id"]
    client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "text": "QA Desk로 이동한다.",
            "intent_hint": {"intent": "move", "location": "qa_desk", "confidence": 1},
        },
    )

    action_response = client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={"text": "QA의 키보드를 빼앗아 던진다.", "target_hint": "qa_01"},
    )

    assert action_response.status_code == 200
    payload = action_response.json()
    assert payload["classified_action"] == "game_action_attempt"
    assert payload["blocked"] is True
    assert payload["alert"] == "Use the provided action buttons to perform game actions."
    assert payload["snapshot"]["turn"] == 1
    qa_keyboard = next(item for item in payload["snapshot"]["world_objects"] if item["id"] == "qa_keyboard")
    assert qa_keyboard["condition"] == "normal"


def test_game_action_button_api_picks_up_backend_keyboard() -> None:
    session_response = client.post("/api/v1/sessions")
    session_id = session_response.json()["session_id"]
    client.post(
        f"/api/v1/sessions/{session_id}/actions",
        json={
            "text": "Dev Area로 이동한다.",
            "intent_hint": {"intent": "move", "location": "dev_area", "confidence": 1},
        },
    )

    action_response = client.post(
        f"/api/v1/sessions/{session_id}/game-actions",
        json={"action_id": "pick_up_backend_keyboard"},
    )

    assert action_response.status_code == 200
    payload = action_response.json()
    assert payload["blocked"] is False
    keyboard = next(item for item in payload["snapshot"]["world_objects"] if item["id"] == "backend_keyboard")
    assert keyboard["holder_id"] == "player"
    assert "backend_keyboard" in payload["snapshot"]["player_inventory"]["held_object_ids"]


@pytest.mark.parametrize("endpoint, method, payload", [
    ("actions", "submit_action", {"text": "질문합니다"}),
    ("game-actions", "submit_game_action", {"action_id": "test"}),
    ("report", "submit_report", {"primary_cause": "test"}),
    ("reset", "reset_session", None),
])
def test_session_conflicts_return_409(monkeypatch, endpoint, method, payload):
    from app.main import engine
    from app.storage import SessionConflictError

    def conflict(*args, **kwargs):
        raise SessionConflictError("Session changed; reload before retrying.")

    monkeypatch.setattr(engine, method, conflict)
    response = client.post(f"/api/v1/sessions/test/{endpoint}", json=payload)
    assert response.status_code == 409
    assert "reload" in response.json()["detail"]

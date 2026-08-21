from fastapi.testclient import TestClient

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

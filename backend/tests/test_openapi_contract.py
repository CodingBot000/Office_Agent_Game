from app.main import app


def test_openapi_contains_unity_client_contract() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/api/v1/sessions" in paths
    assert "/api/v1/sessions/{session_id}" in paths
    assert "/api/v1/sessions/{session_id}/actions" in paths
    assert "/api/v1/sessions/{session_id}/report" in paths
    assert "/api/v1/sessions/{session_id}/reset" in paths

    action_request = schema["components"]["schemas"]["ActionRequest"]
    assert {"text", "intent_hint", "target_hint"}.issubset(action_request["properties"])

    snapshot = schema["components"]["schemas"]["GameSnapshot"]
    required = set(snapshot["required"])
    assert {"session_id", "turn", "current_location", "npcs", "evidences", "events", "agent_traces", "fallback_notices"}.issubset(required)

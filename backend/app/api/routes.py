from fastapi import APIRouter, HTTPException, status

from app.game.engine import GameEngine, InvalidIntentHintError, SessionNotFoundError
from app.models import ActionRequest, ActionResponse, GameActionRequest, GameActionResponse, GameSnapshot, IncidentReportRequest


def create_router(engine: GameEngine) -> APIRouter:
    router = APIRouter(tags=["game"])

    @router.post("/sessions", response_model=GameSnapshot, status_code=status.HTTP_201_CREATED)
    def start_session() -> GameSnapshot:
        return engine.create_session()

    @router.get("/sessions/{session_id}", response_model=GameSnapshot)
    def get_session(session_id: str) -> GameSnapshot:
        try:
            return engine.snapshot(engine.get_session(session_id))
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @router.post("/sessions/{session_id}/actions", response_model=ActionResponse)
    def submit_action(session_id: str, request: ActionRequest) -> ActionResponse:
        try:
            return engine.submit_action(session_id, request.text, request.intent_hint, request.target_hint)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except InvalidIntentHintError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/sessions/{session_id}/game-actions", response_model=GameActionResponse)
    def submit_game_action(session_id: str, request: GameActionRequest) -> GameActionResponse:
        try:
            return engine.submit_game_action(session_id, request)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @router.post("/sessions/{session_id}/report", response_model=GameSnapshot)
    def submit_report(session_id: str, request: IncidentReportRequest) -> GameSnapshot:
        try:
            return engine.submit_report(session_id, request)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @router.post("/sessions/{session_id}/reset", response_model=GameSnapshot)
    def reset_session(session_id: str) -> GameSnapshot:
        try:
            return engine.reset_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    return router

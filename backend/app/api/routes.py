from fastapi import APIRouter, HTTPException, status

from app.game.engine import GameEngine, SessionNotFoundError
from app.models import ActionRequest, ActionResponse, GameSnapshot, IncidentReportRequest


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
            return engine.submit_action(session_id, request.text)
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

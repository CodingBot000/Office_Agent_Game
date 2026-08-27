"""Structured event creation shared by domain transitions."""
from __future__ import annotations
from datetime import UTC, datetime
from app.game.session import GameSession
from app.models import EventLogEntry


def append_event(session: GameSession, actor: str, message: str, event_type: str, actor_id: str | None = None,
                  *, evidence_id: str | None = None, recipient_npc_id: str | None = None,
                  evidence_operation: str | None = None) -> None:
    session.events.append(
        EventLogEntry(
            id=len(session.events) + 1,
            turn=session.turn,
            actor=actor,
            actor_id=actor_id,
            message=message,
            event_type=event_type,
            evidence_id=evidence_id, recipient_npc_id=recipient_npc_id, evidence_operation=evidence_operation,
            created_at=datetime.now(UTC),
        )
    )


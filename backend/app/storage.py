from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Protocol

from app.config import Settings


class SessionRepository(Protocol):
    def save(self, session_id: str, payload: dict[str, object]) -> None: ...

    def load(self, session_id: str) -> dict[str, object] | None: ...

    def delete(self, session_id: str) -> None: ...


class MemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, object]] = {}

    def save(self, session_id: str, payload: dict[str, object]) -> None:
        self._sessions[session_id] = deepcopy(payload)

    def load(self, session_id: str) -> dict[str, object] | None:
        payload = self._sessions.get(session_id)
        return deepcopy(payload) if payload is not None else None

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class SQLiteSessionRepository:
    def __init__(self, database_path: str) -> None:
        path = Path(database_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = path
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS game_sessions (
                    session_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save(self, session_id: str, payload: dict[str, object]) -> None:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO game_sessions(session_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, serialized),
            )

    def load(self, session_id: str) -> dict[str, object] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM game_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM game_sessions WHERE session_id = ?", (session_id,))


def create_session_repository(settings: Settings) -> SessionRepository:
    if settings.session_storage == "sqlite":
        return SQLiteSessionRepository(settings.sqlite_path)
    return MemorySessionRepository()

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Protocol

from app.config import Settings


class SessionConflictError(RuntimeError):
    """The session changed or was removed after the caller read it."""


class SessionRepository(Protocol):
    def save(self, session_id: str, payload: dict[str, object], *, expected_revision: int | None) -> int: ...

    def load(self, session_id: str) -> dict[str, object] | None: ...

    def replace(self, session_id: str, expected_revision: int, new_id: str, payload: dict[str, object]) -> int: ...


class MemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, object]] = {}
        self._lock = RLock()

    def save(self, session_id: str, payload: dict[str, object], *, expected_revision: int | None) -> int:
        with self._lock:
            current = self._sessions.get(session_id)
            actual_revision = current["_revision"] if current is not None else None
            if actual_revision != expected_revision:
                raise SessionConflictError("Session changed; reload before retrying.")
            revision = (expected_revision or 0) + 1
            self._sessions[session_id] = deepcopy({**payload, "_revision": revision})
            return revision

    def load(self, session_id: str) -> dict[str, object] | None:
        with self._lock:
            payload = self._sessions.get(session_id)
            return deepcopy(payload) if payload is not None else None

    def replace(self, session_id: str, expected_revision: int, new_id: str, payload: dict[str, object]) -> int:
        with self._lock:
            current = self._sessions.get(session_id)
            if current is None or current["_revision"] != expected_revision or new_id in self._sessions:
                raise SessionConflictError("Session changed; reload before resetting.")
            replacement = deepcopy({**payload, "_revision": 1})
            self._sessions[new_id] = replacement
            del self._sessions[session_id]
            return 1


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
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS game_sessions (
                    session_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    revision INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(game_sessions)")}
            if "revision" not in columns:
                connection.execute("ALTER TABLE game_sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")

    def save(self, session_id: str, payload: dict[str, object], *, expected_revision: int | None) -> int:
        serialized = self._encode(payload)
        with self._lock, self._connect() as connection:
            if expected_revision is None:
                try:
                    connection.execute("INSERT INTO game_sessions(session_id, payload, revision) VALUES (?, ?, 1)", (session_id, serialized))
                except sqlite3.IntegrityError as exc:
                    raise SessionConflictError("Session already exists.") from exc
                return 1
            cursor = connection.execute(
                "UPDATE game_sessions SET payload=?, revision=revision+1, updated_at=CURRENT_TIMESTAMP WHERE session_id=? AND revision=?",
                (serialized, session_id, expected_revision))
            if cursor.rowcount != 1:
                raise SessionConflictError("Session changed; reload before retrying.")
            return expected_revision + 1

    def load(self, session_id: str) -> dict[str, object] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload, revision FROM game_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {**json.loads(row["payload"]), "_revision": row["revision"]}

    def replace(self, session_id: str, expected_revision: int, new_id: str, payload: dict[str, object]) -> int:
        serialized = self._encode(payload)
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM game_sessions WHERE session_id=? AND revision=?", (session_id, expected_revision))
            if cursor.rowcount != 1:
                raise SessionConflictError("Session changed; reload before resetting.")
            try:
                connection.execute("INSERT INTO game_sessions(session_id, payload, revision) VALUES (?, ?, 1)", (new_id, serialized))
            except sqlite3.IntegrityError as exc:
                raise SessionConflictError("Replacement session already exists.") from exc
            return 1

    @staticmethod
    def _encode(payload: dict[str, object]) -> str:
        return json.dumps({key: value for key, value in payload.items() if key != "_revision"}, ensure_ascii=False, separators=(",", ":"))


def create_session_repository(settings: Settings) -> SessionRepository:
    if settings.session_storage == "sqlite":
        return SQLiteSessionRepository(settings.sqlite_path)
    return MemorySessionRepository()

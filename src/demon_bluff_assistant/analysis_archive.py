from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from demon_bluff_assistant.models import Advice, GameState, SolverReport, StatePatch


class AnalysisArchive:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recognition_records (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_recognition_session
                    ON recognition_records(session_id, created_at);

                CREATE TABLE IF NOT EXISTS analysis_records (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    recognition_id TEXT,
                    created_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    advice_json TEXT NOT NULL,
                    FOREIGN KEY(recognition_id) REFERENCES recognition_records(id)
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_session
                    ON analysis_records(session_id, created_at);

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_session
                    ON chat_messages(session_id, id);
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(recognition_records)"
                ).fetchall()
            }
            if "state_json" not in columns:
                connection.execute(
                    "ALTER TABLE recognition_records ADD COLUMN state_json TEXT"
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record_recognition(
        self, session_id: str, patch: StatePatch, state: GameState
    ) -> str:
        record_id = uuid4().hex
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO recognition_records(
                    id, session_id, created_at, payload_json, state_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    session_id,
                    self._now(),
                    patch.model_dump_json(),
                    state.model_dump_json(),
                ),
            )
        return record_id

    def record_analysis(
        self,
        session_id: str,
        state: GameState,
        report: SolverReport,
        advice: Advice,
    ) -> str:
        record_id = uuid4().hex
        with self._lock, self._connect() as connection:
            recognition = connection.execute(
                """
                SELECT id FROM recognition_records
                WHERE session_id = ? AND state_json = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id, state.model_dump_json()),
            ).fetchone()
            connection.execute(
                "INSERT INTO analysis_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    session_id,
                    recognition["id"] if recognition else None,
                    self._now(),
                    state.model_dump_json(),
                    report.model_dump_json(),
                    advice.model_dump_json(),
                ),
            )
        return record_id

    def export_latest(self, session_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.*, r.payload_json AS recognition_json
                FROM analysis_records a
                LEFT JOIN recognition_records r ON r.id = a.recognition_id
                WHERE a.session_id = ? ORDER BY a.created_at DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return self._export_row(row) if row else None

    def export_dataset(self) -> dict:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, r.payload_json AS recognition_json
                FROM analysis_records a
                LEFT JOIN recognition_records r ON r.id = a.recognition_id
                ORDER BY a.created_at
                """
            ).fetchall()
        return {
            "schema_version": 1,
            "records": [self._export_row(row) for row in rows],
        }

    @staticmethod
    def _export_row(row: sqlite3.Row) -> dict:
        return {
            "schema_version": 1,
            "analysis_id": row["id"],
            "recognition_id": row["recognition_id"],
            "session_id": row["session_id"],
            "created_at": row["created_at"],
            "confirmed_recognition": (
                json.loads(row["recognition_json"])
                if row["recognition_json"]
                else None
            ),
            "confirmed_state": json.loads(row["state_json"]),
            "analysis": {
                "report": json.loads(row["report_json"]),
                "advice": json.loads(row["advice_json"]),
            },
        }

    def chat_history(self, session_id: str, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at FROM (
                    SELECT id, role, content, created_at FROM chat_messages
                    WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_chat_exchange(
        self, session_id: str, user_message: str, assistant_message: str
    ) -> None:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO chat_messages(session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (session_id, "user", user_message, now),
                    (session_id, "assistant", assistant_message, now),
                ],
            )

    def clear_chat(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM chat_messages WHERE session_id = ?", (session_id,)
            )

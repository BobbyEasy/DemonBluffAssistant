from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from demon_bluff_assistant.models import GameState, SeatState, StatePatch, VillageConfig


class SessionNotFound(KeyError):
    pass


class SessionStore:
    def __init__(self, data_dir: Path) -> None:
        self.sessions_dir = Path(data_dir) / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, config: VillageConfig) -> GameState:
        state = GameState(
            config=config,
            seats=[SeatState(position=i) for i in range(1, config.card_count + 1)],
        )
        self._write_payload(state.session_id, {"current": state.model_dump(mode="json"), "history": []})
        return state

    def get(self, session_id: str) -> GameState:
        return GameState.model_validate(self._read_payload(session_id)["current"])

    def apply_patch(self, session_id: str, patch: StatePatch) -> GameState:
        with self._lock:
            payload = self._read_payload(session_id)
            current = GameState.model_validate(payload["current"])
            previous = current.model_dump(mode="json")
            seats = {seat.position: seat for seat in current.seats}
            for incoming in patch.seats:
                if incoming.position not in seats:
                    raise ValueError(f"unknown seat position: {incoming.position}")
                values = seats[incoming.position].model_dump()
                for field in incoming.model_fields_set:
                    if field != "position":
                        values[field] = getattr(incoming, field)
                seats[incoming.position] = SeatState.model_validate(values)
            current.seats = [seats[position] for position in sorted(seats)]
            current.events.extend(patch.events)
            current = GameState.model_validate(current.model_dump())
            payload["history"].append(previous)
            payload["current"] = current.model_dump(mode="json")
            self._write_payload(session_id, payload)
            return current

    def undo(self, session_id: str) -> GameState:
        with self._lock:
            payload = self._read_payload(session_id)
            if payload["history"]:
                payload["current"] = payload["history"].pop()
                self._write_payload(session_id, payload)
            return GameState.model_validate(payload["current"])

    def export_state(self, session_id: str) -> dict[str, Any]:
        return self.get(session_id).model_dump(mode="json")

    def import_state(self, value: dict[str, Any]) -> GameState:
        state = GameState.model_validate(value)
        state.session_id = uuid4().hex
        self._write_payload(
            state.session_id,
            {"current": state.model_dump(mode="json"), "history": []},
        )
        return state

    def _path(self, session_id: str) -> Path:
        if len(session_id) != 32 or any(
            char not in "0123456789abcdef" for char in session_id.casefold()
        ):
            raise SessionNotFound(session_id)
        return self.sessions_dir / f"{session_id}.json"

    def _read_payload(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SessionNotFound(session_id) from exc

    def _write_payload(self, session_id: str, payload: dict[str, Any]) -> None:
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

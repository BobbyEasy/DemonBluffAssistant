from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class Phase(StrEnum):
    START = "start"
    DAY = "day"
    NIGHT = "night"
    EXECUTION = "execution"


class ObservationKind(StrEnum):
    EVIL_COUNT = "evil_count"
    ANY_EVIL = "any_evil"
    GOOD_SEAT = "good_seat"
    ADJACENT_EVIL_COUNT = "adjacent_evil_count"
    NEAREST_EVIL_DISTANCE = "nearest_evil_distance"
    SIDE_MORE_EVIL = "side_more_evil"
    LIAR_STATUS = "liar_status"
    CORRUPTION_DISTANCE = "corruption_distance"
    SUSPICIOUS_TYPE = "suspicious_type"
    ROLE_CLAIM = "role_claim"
    FREE_TEXT = "free_text"


class SuspicionClass(StrEnum):
    CERTAIN_EVIL = "certain_evil"
    LEAN_EVIL = "lean_evil"
    UNDETERMINED = "undetermined"
    CERTAIN_GOOD = "certain_good"


class ActionType(StrEnum):
    REVEAL = "reveal"
    USE_ABILITY = "use_ability"
    EXECUTE = "execute"
    WAIT = "wait"


class VillageConfig(BaseModel):
    language: str = "zh-Hans"
    card_count: int = Field(ge=3, le=20)
    evil_count: int = Field(ge=0)
    evil_count_max: int | None = Field(default=None, ge=0)
    minion_count: int = Field(ge=0)
    minion_count_max: int | None = Field(default=None, ge=0)
    demon_count: int = Field(ge=0)
    demon_count_max: int | None = Field(default=None, ge=0)
    health: int = Field(default=10, ge=0)
    deck_roles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> "VillageConfig":
        if self.evil_count_max is None:
            self.evil_count_max = self.evil_count
        if self.minion_count_max is None:
            self.minion_count_max = self.minion_count
        if self.demon_count_max is None:
            self.demon_count_max = self.demon_count
        ranges = [
            ("evil", self.evil_count, self.evil_count_max),
            ("minion", self.minion_count, self.minion_count_max),
            ("demon", self.demon_count, self.demon_count_max),
        ]
        for name, minimum, maximum in ranges:
            if maximum < minimum:
                raise ValueError(f"{name} maximum must not be lower than minimum")
        if self.evil_count_max >= self.card_count:
            raise ValueError("evil_count_max must be lower than card_count")
        if self.minion_count + self.demon_count > self.evil_count_max:
            raise ValueError("minimum minion and demon counts exceed evil range")
        if self.minion_count_max + self.demon_count_max < self.evil_count:
            raise ValueError("minion and demon ranges cannot reach evil minimum")
        return self


class SeatState(BaseModel):
    position: int = Field(ge=1)
    visible_role: str | None = None
    revealed: bool = False
    alive: bool = True
    corrupted: bool = False
    confirmed_alignment: str | None = None
    claim_text: str | None = None


class ObservationEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    speaker_position: int = Field(ge=1)
    role_id: str | None = None
    phase: Phase = Phase.DAY
    kind: ObservationKind
    targets: list[int] = Field(default_factory=list)
    value: int | bool | str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_targets(self) -> "ObservationEvent":
        self.targets = sorted(set(self.targets))
        return self


class GameState(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    config: VillageConfig
    seats: list[SeatState] = Field(default_factory=list)
    events: list[ObservationEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_positions(self) -> "GameState":
        positions = [seat.position for seat in self.seats]
        if len(positions) != len(set(positions)):
            raise ValueError("seat positions must be unique")
        valid = set(range(1, self.config.card_count + 1))
        if not set(positions).issubset(valid):
            raise ValueError("seat position is outside the village")
        for event in self.events:
            if event.speaker_position not in valid or not set(event.targets).issubset(valid):
                raise ValueError("event references a seat outside the village")
        return self


class StatePatch(BaseModel):
    seats: list[SeatState] = Field(default_factory=list)
    events: list[ObservationEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recognition_engine: str | None = None
    raw_text: list[str] = Field(default_factory=list)


class VillageSetupSuggestion(BaseModel):
    config: VillageConfig | None = None
    warnings: list[str] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recognition_engine: str | None = None
    raw_text: list[str] = Field(default_factory=list)


class SeatAssessment(BaseModel):
    position: int
    classification: SuspicionClass
    consistent_world_share: float = Field(ge=0.0, le=1.0)
    evidence_event_ids: list[str] = Field(default_factory=list)


class LegalAction(BaseModel):
    action_type: ActionType
    positions: list[int] = Field(default_factory=list)
    label: str


class SolverReport(BaseModel):
    satisfiable: bool
    exact: bool = True
    world_count: int = 0
    assessments: list[SeatAssessment] = Field(default_factory=list)
    conflict_event_ids: list[str] = Field(default_factory=list)
    legal_actions: list[LegalAction] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Advice(BaseModel):
    action_type: ActionType
    positions: list[int] = Field(default_factory=list)
    summary: str
    reasoning: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    uncertainty: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

from __future__ import annotations

import pytest
from pydantic import ValidationError

from demon_bluff_assistant.models import (
    GameState,
    ObservationEvent,
    ObservationKind,
    Phase,
    SeatState,
    VillageConfig,
)
from demon_bluff_assistant.roles import RoleCatalog


def test_default_catalog_covers_demo_roles_and_bilingual_aliases() -> None:
    catalog = RoleCatalog.load_default()

    assert len(catalog.roles) >= 40
    assert catalog.resolve("Alchemist").role_id == "alchemist"
    assert catalog.resolve("炼金术士").role_id == "alchemist"
    assert catalog.resolve("双生走卒").role_id == "twin_minion"
    assert catalog.resolve("Lilis").character_type == "demon"


@pytest.mark.parametrize("role_id", RoleCatalog.load_default().roles)
def test_every_role_has_explicit_alignment_and_lie_rule(role_id: str) -> None:
    role = RoleCatalog.load_default().roles[role_id]

    assert role.alignment in {"good", "evil", "dynamic"}
    assert role.character_type in {"villager", "outcast", "minion", "demon"}
    assert role.lie_rule
    assert role.description_en


def test_game_state_rejects_duplicate_or_out_of_range_seats() -> None:
    config = VillageConfig(card_count=3, evil_count=1, minion_count=0, demon_count=1)

    with pytest.raises(ValidationError):
        GameState(
            config=config,
            seats=[SeatState(position=1), SeatState(position=1)],
        )

    with pytest.raises(ValidationError):
        GameState(config=config, seats=[SeatState(position=4)])


def test_village_allows_zero_demons_when_evils_are_all_minions() -> None:
    config = VillageConfig(
        card_count=8,
        evil_count=2,
        minion_count=2,
        demon_count=0,
    )

    assert config.demon_count == 0


def test_village_allows_zero_total_evils() -> None:
    config = VillageConfig(
        card_count=8,
        evil_count=0,
        minion_count=0,
        demon_count=0,
    )

    assert config.evil_count == 0


def test_observation_event_normalizes_targets_and_confidence() -> None:
    event = ObservationEvent(
        speaker_position=2,
        role_id="jester",
        phase=Phase.DAY,
        kind=ObservationKind.EVIL_COUNT,
        targets=[3, 1, 3],
        value=1,
        confidence=0.92,
    )

    assert event.targets == [1, 3]
    assert event.confidence == 0.92

    with pytest.raises(ValidationError):
        ObservationEvent(
            speaker_position=1,
            role_id="lover",
            kind=ObservationKind.ADJACENT_EVIL_COUNT,
            value=0,
            confidence=1.1,
        )

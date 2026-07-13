from __future__ import annotations

from demon_bluff_assistant.local_vision import (
    LocalGameParser,
    LocalVisionService,
    OcrDocument,
    OcrToken,
)
from demon_bluff_assistant.models import GameState, VillageConfig


def token(text: str, x: float, y: float, confidence: float = 0.95) -> OcrToken:
    return OcrToken(
        text=text,
        confidence=confidence,
        left=x,
        top=y,
        right=x + 120,
        bottom=y + 30,
    )


def test_local_parser_creates_village_from_bilingual_labels_and_roles() -> None:
    document = OcrDocument(
        width=1200,
        height=800,
        tokens=[
            token("牌数 9", 20, 20),
            token("恶徒 2", 180, 20),
            token("走卒 1", 340, 20),
            token("恶魔 1", 500, 20),
            token("生命 10", 660, 20),
            token("Fortune TeIler", 100, 200, 0.92),
            token("炼金术士", 600, 200, 0.96),
        ],
    )

    result = LocalGameParser().parse_village(document)

    assert result.config is not None
    assert result.config.card_count == 9
    assert result.config.evil_count == 2
    assert result.config.minion_count == 1
    assert result.config.demon_count == 1
    assert result.config.health == 10
    assert {"fortune_teller", "alchemist"}.issubset(result.config.deck_roles)
    assert "minion" not in result.config.deck_roles
    assert result.recognition_engine == "rapidocr-local"


def test_local_parser_extracts_multiple_roles_from_one_ocr_line() -> None:
    document = OcrDocument(
        width=1200,
        height=800,
        tokens=[
            token("Cards 9 Evil 2 Minions 1 Demons 1 Health 10", 20, 20),
            token("Fortune Teller Drunk Slayer", 100, 200),
        ],
    )

    result = LocalGameParser().parse_village(document)

    assert result.config is not None
    assert {"fortune_teller", "drunk", "slayer"}.issubset(
        result.config.deck_roles
    )


def test_local_parser_understands_real_chinese_objective_layout() -> None:
    document = OcrDocument(
        width=2560,
        height=1440,
        tokens=[
            token("找出并处决2名恶徒", 101, 96, 0.999),
            token("(2个爪牙和0个恶魔)", 116, 141, 0.997),
            token("杀死恶徒：0/2", 107, 170, 0.999),
            token("#8", 1252, 62, 0.990),
            token("#7", 919, 200, 0.997),
            token("#1", 1583, 200, 0.997),
            token("#6", 782, 533, 0.988),
            token("#2", 1720, 532, 0.997),
            token("#5", 919, 863, 0.996),
            token("#3", 1583, 865, 0.996),
            token("#4", 1252, 1002, 0.990),
            token("10", 237, 1099, 1.0),
        ],
    )

    result = LocalGameParser().parse_village(document)

    assert result.config is not None
    assert result.config.card_count == 8
    assert result.config.evil_count == 2
    assert result.config.minion_count == 2
    assert result.config.demon_count == 0
    assert result.config.health == 10
    assert result.warnings == []


def test_local_parser_uses_execution_progress_total_as_evil_count() -> None:
    document = OcrDocument(
        width=1200,
        height=800,
        tokens=[
            token("杀死恶徒：0/2", 20, 20),
            token("2个爪牙和0个恶魔", 20, 60),
            *[token(f"#{position}", position * 100, 200) for position in range(1, 9)],
            token("10", 60, 650),
        ],
    )

    result = LocalGameParser().parse_village(document)

    assert result.config is not None
    assert result.config.evil_count == 2


def test_local_parser_refuses_to_guess_missing_core_counts() -> None:
    document = OcrDocument(
        width=800,
        height=600,
        tokens=[token("Cards 9", 10, 10), token("Health 10", 200, 10)],
    )

    result = LocalGameParser().parse_village(document)

    assert result.config is None
    assert any("恶徒" in warning for warning in result.warnings)


def test_local_parser_pairs_role_with_nearest_seat_and_keeps_claim_text() -> None:
    state = GameState(
        config=VillageConfig(
            card_count=3, evil_count=1, minion_count=0, demon_count=1
        )
    )
    document = OcrDocument(
        width=1000,
        height=700,
        tokens=[
            token("#1", 80, 100),
            token("Fortune TeIler", 90, 145, 0.93),
            token("目标中有恶徒", 90, 185, 0.90),
            token("#2", 700, 100),
            token("Alchemist", 710, 145, 0.97),
        ],
    )

    patch = LocalGameParser().parse_state(document, state)

    seats = {seat.position: seat for seat in patch.seats}
    assert seats[1].visible_role == "Fortune Teller"
    assert "目标中有恶徒" in seats[1].claim_text
    assert seats[2].visible_role == "Alchemist"
    assert patch.overall_confidence > 0.8


class FakeEngine:
    def recognize(self, png_bytes: bytes) -> OcrDocument:
        assert png_bytes == b"png"
        return OcrDocument(
            width=800,
            height=600,
            tokens=[token("Cards 3 Evil 1 Minion 0 Demon 1 Health 10", 10, 10)],
        )


def test_local_service_uses_offline_engine_without_model_api() -> None:
    service = LocalVisionService(engine=FakeEngine())

    result = service.parse_village(b"png")

    assert result.config.card_count == 3
    assert result.config.evil_count == 1

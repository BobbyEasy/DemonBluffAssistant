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


def centered_token(
    text: str, center_x: float, center_y: float, confidence: float = 0.99
) -> OcrToken:
    return token(text, center_x - 60, center_y - 15, confidence)


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


def test_local_parser_uses_card_geometry_for_real_round_table_layout() -> None:
    state = GameState(
        config=VillageConfig(
            card_count=8, evil_count=2, minion_count=2, demon_count=0
        )
    )
    document = OcrDocument(
        width=2560,
        height=1440,
        tokens=[
            centered_token("#8", 1281, 82), centered_token("#7", 947, 219),
            centered_token("#1", 1612, 220), centered_token("有1对恶徒", 746, 290),
            centered_token("建筑师", 1280, 293), centered_token("左边有更多恶", 1810, 290),
            centered_token("两两相邻", 745, 325), centered_token("徒", 1812, 325),
            centered_token("左边有更多恶", 1281, 377), centered_token("徒", 1281, 412),
            centered_token("针织者", 948, 430), centered_token("建筑师", 1612, 430),
            centered_token("#6", 810, 552), centered_token("#2", 1749, 552),
            centered_token("#1、#3、#8", 608, 587), centered_token("之中有：", 601, 622),
            centered_token("爪牙、村民和", 608, 658), centered_token("边缘人", 608, 693),
            centered_token("主教", 810, 763), centered_token("#5", 948, 884),
            centered_token("#3", 1612, 884), centered_token("#6是真的", 1281, 922),
            centered_token("主教", 1282, 958), centered_token("#4", 1281, 1022),
            centered_token("倒霉鬼", 947, 1095), centered_token("骑士", 1612, 1095),
            centered_token("灵媒", 1280, 1232),
        ],
    )

    patch = LocalGameParser().parse_state(document, state)

    seats = {seat.position: seat for seat in patch.seats}
    assert set(seats) == {1, 3, 4, 5, 6, 7, 8}
    assert {position: seat.visible_role for position, seat in seats.items()} == {
        1: "Architect", 3: "Knight", 4: "Medium", 5: "Wretch",
        6: "Bishop", 7: "Knitter", 8: "Architect",
    }
    assert seats[1].claim_text == "左边有更多恶徒"
    assert seats[8].claim_text == "左边有更多恶徒"
    assert seats[7].claim_text == "有1对恶徒 两两相邻"
    assert seats[6].claim_text == "#1、#3、#8 之中有： 爪牙、村民和 边缘人"
    assert seats[4].claim_text == "#6是真的 主教"
    assert seats[3].claim_text is None
    assert seats[5].claim_text is None


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

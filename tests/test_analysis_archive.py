from __future__ import annotations

from demon_bluff_assistant.analysis_archive import AnalysisArchive
from demon_bluff_assistant.models import Advice, GameState, SeatState, SolverReport, StatePatch, VillageConfig


def test_archive_pairs_confirmed_recognition_with_analysis(tmp_path) -> None:
    archive = AnalysisArchive(tmp_path / "analysis.db")
    state = GameState(
        config=VillageConfig(
            card_count=3, evil_count=1, minion_count=0, demon_count=1
        ),
        seats=[SeatState(position=1, visible_role="Architect")],
    )
    patch = StatePatch(
        seats=[{"position": 1, "visible_role": "Architect"}],
        recognition_engine="glm-4.6v-flash",
        overall_confidence=0.9,
    )

    recognition_id = archive.record_recognition(state.session_id, patch, state)
    analysis_id = archive.record_analysis(
        state.session_id,
        state,
        SolverReport(satisfiable=True, world_count=3),
        Advice(action_type="wait", summary="继续取证", uncertainty="信息不足"),
    )
    exported = archive.export_latest(state.session_id)

    assert exported is not None
    assert exported["analysis_id"] == analysis_id
    assert exported["recognition_id"] == recognition_id
    assert exported["confirmed_recognition"]["recognition_engine"] == "glm-4.6v-flash"
    assert exported["analysis"]["advice"]["summary"] == "继续取证"


def test_archive_does_not_pair_recognition_from_a_different_state(tmp_path) -> None:
    archive = AnalysisArchive(tmp_path / "analysis.db")
    original = GameState(
        config=VillageConfig(
            card_count=3, evil_count=1, minion_count=0, demon_count=1
        )
    )
    recognized = original.model_copy(deep=True)
    recognized.seats = [SeatState(position=1, visible_role="Architect")]
    archive.record_recognition(
        original.session_id,
        StatePatch(seats=recognized.seats),
        recognized,
    )

    archive.record_analysis(
        original.session_id,
        original,
        SolverReport(satisfiable=True),
        Advice(action_type="wait", summary="已撤销", uncertainty="信息不足"),
    )

    assert archive.export_latest(original.session_id)["recognition_id"] is None


def test_archive_persists_chat_history_and_can_clear_it(tmp_path) -> None:
    archive = AnalysisArchive(tmp_path / "analysis.db")

    archive.add_chat_exchange("session", "先翻谁？", "优先补充 #2 信息。")

    assert [item["role"] for item in archive.chat_history("session")] == [
        "user",
        "assistant",
    ]
    archive.clear_chat("session")
    assert archive.chat_history("session") == []

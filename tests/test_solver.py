from __future__ import annotations

from demon_bluff_assistant.models import (
    ActionType,
    GameState,
    ObservationEvent,
    ObservationKind,
    SeatState,
    SuspicionClass,
    VillageConfig,
)
from demon_bluff_assistant.solver import WorldSolver


def make_state(*, card_count: int = 3, evil_count: int = 1) -> GameState:
    return GameState(
        config=VillageConfig(
            card_count=card_count,
            evil_count=evil_count,
            minion_count=max(0, evil_count - 1),
            demon_count=1,
        ),
        seats=[SeatState(position=i) for i in range(1, card_count + 1)],
    )


def assessment(report, position: int):
    return next(item for item in report.assessments if item.position == position)


def test_truthful_claim_can_prove_an_evil_and_safe_execution() -> None:
    state = make_state()
    state.seats[0].confirmed_alignment = "good"
    state.seats[0].revealed = True
    state.events.append(
        ObservationEvent(
            event_id="proof",
            speaker_position=1,
            role_id="fortune_teller",
            kind=ObservationKind.ANY_EVIL,
            targets=[2],
            value=True,
        )
    )

    report = WorldSolver().solve(state)

    assert report.satisfiable
    assert report.world_count == 1
    assert assessment(report, 2).classification == SuspicionClass.CERTAIN_EVIL
    assert assessment(report, 3).classification == SuspicionClass.CERTAIN_GOOD
    assert any(
        action.action_type == ActionType.EXECUTE and action.positions == [2]
        for action in report.legal_actions
    )
    assert not any(
        action.action_type == ActionType.EXECUTE and action.positions == [3]
        for action in report.legal_actions
    )


def test_evil_or_corrupted_speaker_makes_generic_information_false() -> None:
    for corrupted, alignment in [(False, "evil"), (True, "good")]:
        state = make_state()
        state.seats[0].confirmed_alignment = alignment
        state.seats[0].corrupted = corrupted
        state.events.append(
            ObservationEvent(
                speaker_position=1,
                role_id="fortune_teller",
                kind=ObservationKind.ANY_EVIL,
                targets=[2],
                value=True,
            )
        )

        report = WorldSolver().solve(state)

        assert report.satisfiable
        assert assessment(report, 2).classification == SuspicionClass.CERTAIN_GOOD


def test_conflicting_events_return_their_ids_in_unsat_core() -> None:
    state = make_state()
    state.seats[0].confirmed_alignment = "good"
    state.events.extend(
        [
            ObservationEvent(
                event_id="says-evil",
                speaker_position=1,
                role_id="fortune_teller",
                kind=ObservationKind.ANY_EVIL,
                targets=[2],
                value=True,
            ),
            ObservationEvent(
                event_id="says-good",
                speaker_position=1,
                role_id="fortune_teller",
                kind=ObservationKind.ANY_EVIL,
                targets=[2],
                value=False,
            ),
        ]
    )

    report = WorldSolver().solve(state)

    assert not report.satisfiable
    assert set(report.conflict_event_ids) == {"says-evil", "says-good"}
    assert not report.legal_actions


def test_duplicate_villager_claims_prove_other_seat_good() -> None:
    state = make_state()
    state.seats[0].visible_role = "Alchemist"
    state.seats[1].visible_role = "炼金术士"

    report = WorldSolver().solve(state)

    assert report.satisfiable
    assert assessment(report, 3).classification == SuspicionClass.CERTAIN_GOOD
    assert assessment(report, 1).classification == SuspicionClass.UNDETERMINED
    assert assessment(report, 2).classification == SuspicionClass.UNDETERMINED


def test_world_limit_marks_result_as_sampled() -> None:
    state = make_state(card_count=6, evil_count=3)

    report = WorldSolver(world_limit=5).solve(state)

    assert report.satisfiable
    assert report.world_count == 5
    assert not report.exact
    assert any("抽样" in note for note in report.notes)


def test_sampled_worlds_never_create_false_certain_classifications() -> None:
    state = make_state(card_count=6, evil_count=3)

    report = WorldSolver(world_limit=1).solve(state)

    assert not report.exact
    assert all(
        item.classification
        not in {SuspicionClass.CERTAIN_EVIL, SuspicionClass.CERTAIN_GOOD}
        for item in report.assessments
    )


def test_empress_special_lie_requires_all_selected_targets_good() -> None:
    state = make_state(card_count=4, evil_count=1)
    state.seats[0].confirmed_alignment = "evil"
    state.events.append(
        ObservationEvent(
            speaker_position=1,
            role_id="empress",
            kind=ObservationKind.EVIL_COUNT,
            targets=[2, 3, 4],
            value=1,
        )
    )

    report = WorldSolver().solve(state)

    assert report.satisfiable
    for position in [2, 3, 4]:
        assert assessment(report, position).classification == SuspicionClass.CERTAIN_GOOD


def test_unimplemented_special_lie_rule_is_not_used_as_a_hard_constraint() -> None:
    state = make_state(card_count=5, evil_count=1)
    state.seats[0].confirmed_alignment = "good"
    state.events.append(
        ObservationEvent(
            event_id="hunter-special",
            speaker_position=1,
            role_id="hunter",
            kind=ObservationKind.NEAREST_EVIL_DISTANCE,
            value=1,
        )
    )

    report = WorldSolver().solve(state)

    assert report.satisfiable
    assert assessment(report, 3).classification == SuspicionClass.UNDETERMINED
    assert any("hunter-special" in note for note in report.notes)


def test_role_and_observation_kind_must_match_before_constraining_worlds() -> None:
    state = make_state()
    state.seats[0].confirmed_alignment = "good"
    state.events.append(
        ObservationEvent(
            event_id="wrong-kind",
            speaker_position=1,
            role_id="fortune_teller",
            kind=ObservationKind.EVIL_COUNT,
            targets=[2],
            value=1,
        )
    )

    report = WorldSolver().solve(state)

    assert report.satisfiable
    assert assessment(report, 2).classification == SuspicionClass.UNDETERMINED
    assert any("wrong-kind" in note for note in report.notes)


def test_certain_assessment_lists_only_events_in_its_z3_proof() -> None:
    state = make_state()
    state.seats[0].confirmed_alignment = "good"
    state.events.extend(
        [
            ObservationEvent(
                event_id="proof-event",
                speaker_position=1,
                role_id="fortune_teller",
                kind=ObservationKind.ANY_EVIL,
                targets=[2],
                value=True,
            ),
            ObservationEvent(
                event_id="ignored-note",
                speaker_position=1,
                role_id="poet",
                kind=ObservationKind.FREE_TEXT,
                raw_text="仅供解释",
            ),
        ]
    )

    report = WorldSolver().solve(state)

    assert assessment(report, 2).evidence_event_ids == ["proof-event"]
    assert "ignored-note" not in assessment(report, 3).evidence_event_ids

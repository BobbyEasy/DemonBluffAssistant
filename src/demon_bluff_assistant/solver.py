from __future__ import annotations

from collections import defaultdict

from z3 import And, Bool, BoolVal, If, Not, Or, Solver, Sum, sat, unsat

from demon_bluff_assistant.models import (
    ActionType,
    GameState,
    LegalAction,
    ObservationEvent,
    ObservationKind,
    SeatAssessment,
    SolverReport,
    SuspicionClass,
)
from demon_bluff_assistant.roles import RoleCatalog


class WorldSolver:
    ROLE_OBSERVATION_KINDS = {
        "architect": ObservationKind.SIDE_MORE_EVIL,
        "confessor": ObservationKind.LIAR_STATUS,
        "empress": ObservationKind.EVIL_COUNT,
        "fortune_teller": ObservationKind.ANY_EVIL,
        "gemcrafter": ObservationKind.GOOD_SEAT,
        "jester": ObservationKind.EVIL_COUNT,
        "judge": ObservationKind.LIAR_STATUS,
        "lover": ObservationKind.ADJACENT_EVIL_COUNT,
        "oracle": ObservationKind.ANY_EVIL,
    }
    EXPLICIT_SPECIAL_RULES = {"empress", "oracle"}

    def __init__(self, world_limit: int = 5_000, catalog: RoleCatalog | None = None) -> None:
        if world_limit < 1:
            raise ValueError("world_limit must be positive")
        self.world_limit = world_limit
        self.catalog = catalog or RoleCatalog.load_default()

    def solve(self, state: GameState) -> SolverReport:
        solver = Solver()
        evil = {
            position: Bool(f"evil_{position}")
            for position in range(1, state.config.card_count + 1)
        }
        notes: list[str] = []

        if state.config.evil_count != state.config.evil_count_max:
            notes.append(
                f"恶徒总数按 {state.config.evil_count}-{state.config.evil_count_max} "
                "逐一枚举；一致世界占比只用于比较，不是真实概率。"
            )

        evil_total = Sum([If(evil[position], 1, 0) for position in evil])
        solver.assert_and_track(
            And(
                evil_total >= state.config.evil_count,
                evil_total <= state.config.evil_count_max,
            ),
            Bool("rule:evil_count"),
        )

        seats = {seat.position: seat for seat in state.seats}
        for position in evil:
            seat = seats.get(position)
            if seat is None or seat.confirmed_alignment is None:
                continue
            if seat.confirmed_alignment == "evil":
                constraint = evil[position]
            elif seat.confirmed_alignment == "good":
                constraint = Not(evil[position])
            else:
                notes.append(f"#{position} 的确认阵营无法识别，已忽略。")
                continue
            solver.assert_and_track(constraint, Bool(f"seat:{position}:alignment"))

        self._add_duplicate_constraints(solver, evil, state, notes)

        for event in state.events:
            constraint = self._event_constraint(event, evil, state)
            if constraint is None:
                notes.append(f"事件 {event.event_id} 暂不参与硬约束，仅供策略解释。")
                continue
            solver.assert_and_track(constraint, Bool(f"event:{event.event_id}"))

        if solver.check() != sat:
            conflict_ids = []
            for item in solver.unsat_core():
                label = str(item)
                if label.startswith("event:"):
                    conflict_ids.append(label.removeprefix("event:"))
            return SolverReport(
                satisfiable=False,
                exact=True,
                world_count=0,
                conflict_event_ids=conflict_ids,
                notes=[*notes, "当前确认信息互相矛盾，请修正冲突事件。"],
            )

        proofs: dict[int, tuple[bool, bool, list[str]]] = {}
        for position, variable in evil.items():
            solver.push()
            solver.add(Not(variable))
            always_evil = solver.check() == unsat
            evil_evidence = (
                self._core_event_ids(solver.unsat_core(), state)
                if always_evil
                else []
            )
            solver.pop()

            solver.push()
            solver.add(variable)
            always_good = solver.check() == unsat
            good_evidence = (
                self._core_event_ids(solver.unsat_core(), state)
                if always_good
                else []
            )
            solver.pop()
            evidence = [
                event.event_id
                for event in state.events
                if event.event_id in {*evil_evidence, *good_evidence}
            ]
            proofs[position] = (always_evil, always_good, evidence)

        worlds: list[set[int]] = []
        while len(worlds) <= self.world_limit and solver.check() == sat:
            model = solver.model()
            world = {
                position
                for position, variable in evil.items()
                if bool(model.eval(variable, model_completion=True))
            }
            worlds.append(world)
            solver.add(
                Or(
                    [
                        variable != BoolVal(position in world)
                        for position, variable in evil.items()
                    ]
                )
            )

        exact = len(worlds) <= self.world_limit
        if not exact:
            worlds = worlds[: self.world_limit]
            notes.append(
                f"一致世界超过 {self.world_limit} 个；嫌疑占比来自抽样，不是精确概率。"
            )

        assessments = self._assess(worlds, state, proofs)
        legal_actions = self._legal_actions(state, assessments)
        return SolverReport(
            satisfiable=True,
            exact=exact,
            world_count=len(worlds),
            assessments=assessments,
            legal_actions=legal_actions,
            notes=notes,
        )

    def _add_duplicate_constraints(self, solver, evil, state, notes: list[str]) -> None:
        claims: dict[str, list[int]] = defaultdict(list)
        for seat in state.seats:
            if not seat.visible_role:
                continue
            try:
                role = self.catalog.resolve(seat.visible_role)
            except KeyError:
                notes.append(f"无法识别角色“{seat.visible_role}”，未应用唯一性规则。")
                continue
            if role.character_type == "villager":
                claims[role.role_id].append(seat.position)

        for role_id, positions in claims.items():
            if len(positions) < 2:
                continue
            solver.assert_and_track(
                Or([evil[position] for position in positions]),
                Bool(f"duplicate:{role_id}"),
            )

    def _speaker_lies(self, event: ObservationEvent, evil, state):
        try:
            role = self.catalog.roles[event.role_id] if event.role_id else None
        except KeyError:
            role = None
        if role and role.lie_rule == "always_truth":
            return BoolVal(False)
        if role and role.lie_rule == "always_lie":
            return BoolVal(True)
        seat = next(
            (item for item in state.seats if item.position == event.speaker_position),
            None,
        )
        corrupted = bool(seat and seat.corrupted)
        return Or(evil[event.speaker_position], BoolVal(corrupted))

    def _event_constraint(self, event: ObservationEvent, evil, state):
        if not event.role_id:
            return None
        role = self.catalog.roles.get(event.role_id)
        expected_kind = self.ROLE_OBSERVATION_KINDS.get(event.role_id)
        if role is None or expected_kind != event.kind:
            return None
        if (
            role.lie_rule not in {"invert", "always_truth", "always_lie"}
            and event.role_id not in self.EXPLICIT_SPECIAL_RULES
        ):
            return None

        liar = self._speaker_lies(event, evil, state)
        target_count = Sum([If(evil[position], 1, 0) for position in event.targets])

        if event.role_id == "empress" and event.targets:
            return If(liar, target_count == 0, target_count == 1)
        if event.role_id == "oracle" and event.targets:
            return If(liar, target_count == 0, target_count == 1)

        statement = self._statement(event, evil, state)
        if statement is None:
            return None
        return If(liar, Not(statement), statement)

    def _statement(self, event: ObservationEvent, evil, state):
        if event.kind == ObservationKind.EVIL_COUNT:
            if not isinstance(event.value, int) or isinstance(event.value, bool):
                return None
            return Sum([If(evil[position], 1, 0) for position in event.targets]) == event.value

        if event.kind == ObservationKind.ANY_EVIL:
            if not isinstance(event.value, bool):
                return None
            any_evil = Or([evil[position] for position in event.targets])
            return any_evil if event.value else Not(any_evil)

        if event.kind == ObservationKind.GOOD_SEAT:
            if not event.targets:
                return None
            return Not(evil[event.targets[0]])

        if event.kind == ObservationKind.ADJACENT_EVIL_COUNT:
            if not isinstance(event.value, int) or isinstance(event.value, bool):
                return None
            left = self._offset(event.speaker_position, -1, state.config.card_count)
            right = self._offset(event.speaker_position, 1, state.config.card_count)
            return Sum([If(evil[left], 1, 0), If(evil[right], 1, 0)]) == event.value

        if event.kind == ObservationKind.NEAREST_EVIL_DISTANCE:
            if not isinstance(event.value, int) or isinstance(event.value, bool):
                return None
            distance = event.value
            at_distance = [
                evil[position]
                for position in evil
                if position != event.speaker_position
                and self._distance(event.speaker_position, position, state.config.card_count)
                == distance
            ]
            closer = [
                Not(evil[position])
                for position in evil
                if position != event.speaker_position
                and self._distance(event.speaker_position, position, state.config.card_count)
                < distance
            ]
            return And(Or(at_distance), And(closer))

        if event.kind == ObservationKind.SIDE_MORE_EVIL:
            if not isinstance(event.value, str):
                return None
            left, right = self._side_positions(
                event.speaker_position, state.config.card_count
            )
            left_count = Sum([If(evil[position], 1, 0) for position in left])
            right_count = Sum([If(evil[position], 1, 0) for position in right])
            value = event.value.casefold()
            if value in {"left", "counterclockwise", "左", "逆时针"}:
                return left_count > right_count
            if value in {"right", "clockwise", "右", "顺时针"}:
                return right_count > left_count
            if value in {"equal", "相等", "一样"}:
                return left_count == right_count
            return None

        if event.kind == ObservationKind.LIAR_STATUS:
            if not event.targets or not isinstance(event.value, bool):
                return None
            target = event.targets[0]
            seat = next((item for item in state.seats if item.position == target), None)
            target_liar = Or(evil[target], BoolVal(bool(seat and seat.corrupted)))
            return target_liar if event.value else Not(target_liar)

        if event.kind == ObservationKind.CORRUPTION_DISTANCE:
            if not isinstance(event.value, int) or isinstance(event.value, bool):
                return None
            corrupted_positions = [seat.position for seat in state.seats if seat.corrupted]
            if not corrupted_positions:
                return BoolVal(False)
            nearest = min(
                self._distance(event.speaker_position, position, state.config.card_count)
                for position in corrupted_positions
            )
            return BoolVal(nearest == event.value)

        return None

    def _assess(
        self,
        worlds: list[set[int]],
        state: GameState,
        proofs: dict[int, tuple[bool, bool, list[str]]],
    ) -> list[SeatAssessment]:
        result = []
        for position in range(1, state.config.card_count + 1):
            share = sum(position in world for world in worlds) / len(worlds)
            always_evil, always_good, evidence_event_ids = proofs[position]
            if always_evil:
                classification = SuspicionClass.CERTAIN_EVIL
            elif always_good:
                classification = SuspicionClass.CERTAIN_GOOD
            elif share > 0.5:
                classification = SuspicionClass.LEAN_EVIL
            else:
                classification = SuspicionClass.UNDETERMINED
            result.append(
                SeatAssessment(
                    position=position,
                    classification=classification,
                    consistent_world_share=round(share, 4),
                    evidence_event_ids=evidence_event_ids
                    if classification
                    in {SuspicionClass.CERTAIN_EVIL, SuspicionClass.CERTAIN_GOOD}
                    else [],
                )
            )
        return result

    @staticmethod
    def _core_event_ids(core, state: GameState) -> list[str]:
        labels = {str(item) for item in core}
        return [
            event.event_id
            for event in state.events
            if f"event:{event.event_id}" in labels
        ]

    def _legal_actions(
        self, state: GameState, assessments: list[SeatAssessment]
    ) -> list[LegalAction]:
        seats = {seat.position: seat for seat in state.seats}
        actions = []
        for assessment in assessments:
            seat = seats.get(assessment.position)
            if seat is None:
                continue
            if seat.alive and not seat.revealed:
                actions.append(
                    LegalAction(
                        action_type=ActionType.REVEAL,
                        positions=[seat.position],
                        label=f"翻开 #{seat.position}",
                    )
                )
            if seat.alive and assessment.classification in {
                SuspicionClass.CERTAIN_EVIL,
                SuspicionClass.LEAN_EVIL,
            }:
                actions.append(
                    LegalAction(
                        action_type=ActionType.EXECUTE,
                        positions=[seat.position],
                        label=f"处决 #{seat.position}",
                    )
                )
        return actions

    @staticmethod
    def _offset(position: int, amount: int, card_count: int) -> int:
        return ((position - 1 + amount) % card_count) + 1

    @staticmethod
    def _distance(first: int, second: int, card_count: int) -> int:
        raw = abs(first - second)
        return min(raw, card_count - raw)

    def _side_positions(self, speaker: int, card_count: int) -> tuple[list[int], list[int]]:
        half = card_count // 2
        left = [self._offset(speaker, -step, card_count) for step in range(1, half + 1)]
        right = [self._offset(speaker, step, card_count) for step in range(1, half + 1)]
        return left, right

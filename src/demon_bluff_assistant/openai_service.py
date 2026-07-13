from __future__ import annotations

import base64
import json
from typing import Any

from openai import OpenAI

from demon_bluff_assistant.config import Settings
from demon_bluff_assistant.model_config import (
    ModelConfigStore,
    ModelProvider,
    ResolvedProfile,
)
from demon_bluff_assistant.models import (
    ActionType,
    Advice,
    GameState,
    SolverReport,
    StatePatch,
    VillageSetupSuggestion,
)
from demon_bluff_assistant.roles import RoleCatalog


class IntegrationUnavailable(RuntimeError):
    pass


class AdviceValidationError(ValueError):
    pass


VISION_INSTRUCTIONS = """
你是 Demon Bluff Demo 的画面抄录器，不负责猜测隐藏身份。
只记录截图中玩家可见且能明确辨认的内容，输出 StatePatch。
牌位按游戏显示数字填写；不确定字段保持默认值，并把原因写入 warnings。
证词必须规范化为 ObservationKind；无法可靠规范化时使用 free_text 并保留 raw_text。
绝不能根据常识补全未显示的牌、阵营或腐化状态。
""".strip()


ADVICE_INSTRUCTIONS = """
你是 Demon Bluff 标准模式的中文策略解释器。
本地 SolverReport 是唯一可信的逻辑结论。只能从 legal_actions 选择动作；不得改变嫌疑分类、
不得虚构牌号或事件。证据必须引用已存在的 event_id。信息不足时明确承认不确定性，优先取证。
输出严格符合 Advice。
""".strip()


VILLAGE_INSTRUCTIONS = """
你是 Demon Bluff Demo 的新村庄配置抄录器，不分析隐藏身份。
只从玩家可见的牌桌总览或牌组页面读取：界面语言、牌数、恶徒总数、爪牙/走卒数、恶魔数、生命和牌组角色。
只有在创建村庄所需的牌数及阵营数量都清楚可见时才填写 config；否则 config 必须为 null，
并在 warnings 中准确说明还需要哪一张画面。不得根据常见默认值猜测。输出严格符合 VillageSetupSuggestion。
""".strip()


class OpenAIService:
    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        model_store: ModelConfigStore | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.model_store = model_store
        self.catalog = RoleCatalog.load_default()

    def _vision_profile(self) -> ResolvedProfile:
        if self.model_store is not None:
            profile = self.model_store.resolve(ModelProvider.OPENAI, self.settings)
        elif self.settings.openai_api_key:
            profile = ResolvedProfile(
                provider=ModelProvider.OPENAI,
                model=self.settings.openai_model,
                base_url="https://api.openai.com/v1",
                api_key=self.settings.openai_api_key,
            )
        else:
            profile = None
        if profile is None:
            raise IntegrationUnavailable(
                "未配置 OpenAI 视觉模型 API Key；请在模型设置中配置后重试。"
            )
        return profile

    def _active_profile(self) -> ResolvedProfile | None:
        if self.model_store is not None:
            return self.model_store.active(self.settings)
        if self.settings.openai_api_key:
            return self._vision_profile()
        return None

    def _client_for(self, profile: ResolvedProfile):
        if self.client is not None:
            return self.client
        return OpenAI(
            api_key=profile.api_key,
            base_url=profile.base_url,
            max_retries=0,
            timeout=45.0,
        )

    def parse_capture(self, png_bytes: bytes, state: GameState) -> StatePatch:
        profile = self._vision_profile()
        client = self._client_for(profile)
        encoded = base64.b64encode(png_bytes).decode("ascii")
        input_value = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "当前已确认局面：\n"
                        + state.model_dump_json(exclude={"events": {"__all__": {"raw_text"}}}),
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{encoded}",
                        "detail": "high",
                    },
                ],
            }
        ]
        parsed = self._parse_with_retry(
            client=client,
            model=profile.model,
            instructions=VISION_INSTRUCTIONS,
            input=input_value,
            text_format=StatePatch,
            store=False,
        )
        return StatePatch.model_validate(parsed)

    def parse_village(self, png_bytes: bytes) -> VillageSetupSuggestion:
        profile = self._vision_profile()
        client = self._client_for(profile)
        encoded = base64.b64encode(png_bytes).decode("ascii")
        parsed = self._parse_with_retry(
            client=client,
            model=profile.model,
            instructions=VILLAGE_INSTRUCTIONS,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "识别这张玩家可见截图并返回新村庄配置。",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{encoded}",
                            "detail": "high",
                        },
                    ],
                }
            ],
            text_format=VillageSetupSuggestion,
            store=False,
        )
        return VillageSetupSuggestion.model_validate(parsed)

    def generate_advice(self, state: GameState, report: SolverReport) -> Advice:
        if not report.satisfiable:
            return Advice(
                action_type=ActionType.WAIT,
                summary="先修正互相冲突的录入信息，不要处决。",
                reasoning=["当前规则约束无解，继续行动会建立在错误局面上。"],
                evidence_event_ids=report.conflict_event_ids,
                uncertainty="无法分析，必须先修正冲突。",
            )
        profile = self._active_profile()
        if profile is None:
            return self.template_advice(report)

        compact = {
            "state": state.model_dump(mode="json"),
            "solver_report": report.model_dump(mode="json"),
            "visible_role_rules": self._visible_role_rules(state),
        }
        client = self._client_for(profile)
        if profile.provider == ModelProvider.OPENAI:
            parsed = self._parse_with_retry(
                client=client,
                model=profile.model,
                instructions=ADVICE_INSTRUCTIONS,
                input=json.dumps(compact, ensure_ascii=False),
                text_format=Advice,
                store=False,
            )
        else:
            parsed = self._compatible_json_with_retry(
                client=client,
                profile=profile,
                payload=compact,
                output_model=Advice,
            )
        advice = Advice.model_validate(parsed)
        self._validate_advice(advice, state, report)
        return advice

    def _visible_role_rules(self, state: GameState) -> list[dict]:
        rules = []
        for seat in state.seats:
            if not seat.visible_role:
                continue
            try:
                role = self.catalog.resolve(seat.visible_role)
            except KeyError:
                continue
            rules.append(
                {
                    "position": seat.position,
                    "role_id": role.role_id,
                    "name_zh": role.name_zh,
                    "name_en": role.name_en,
                    "alignment": role.alignment,
                    "ability_kind": role.ability_kind,
                    "lie_rule": role.lie_rule,
                    "description": role.description_en,
                }
            )
        return rules

    def template_advice(self, report: SolverReport) -> Advice:
        certain_execute = next(
            (
                action
                for action in report.legal_actions
                if action.action_type == ActionType.EXECUTE
                and any(
                    assessment.position in action.positions
                    and assessment.classification.value == "certain_evil"
                    for assessment in report.assessments
                )
            ),
            None,
        )
        action = certain_execute or next(
            (
                item
                for item in report.legal_actions
                if item.action_type == ActionType.REVEAL
            ),
            None,
        )
        if action is None:
            return Advice(
                action_type=ActionType.WAIT,
                summary="当前没有安全的推荐动作。",
                uncertainty="请补充更多可见信息。",
            )
        return Advice(
            action_type=action.action_type,
            positions=action.positions,
            summary=action.label,
            reasoning=["这是本地约束求解器给出的保守建议。"],
            uncertainty="未使用 LLM；仅根据已确认的硬约束生成。",
        )

    def _parse_with_retry(self, *, client, **kwargs):
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = client.responses.parse(**kwargs)
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    raise IntegrationUnavailable("模型没有返回可解析的结构化结果。")
                return parsed
            except Exception as exc:  # SDK exceptions share no stable base across versions
                last_error = exc
        raise IntegrationUnavailable(f"OpenAI 调用失败：{last_error}") from last_error

    def _compatible_json_with_retry(
        self, *, client, profile: ResolvedProfile, payload: dict, output_model
    ):
        schema = output_model.model_json_schema()
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = client.chat.completions.create(
                    model=profile.model,
                    messages=[
                        {
                            "role": "system",
                            "content": ADVICE_INSTRUCTIONS
                            + "\n只输出 JSON，不要使用 Markdown。JSON Schema："
                            + json.dumps(schema, ensure_ascii=False),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ],
                    response_format={"type": "json_object"},
                    stream=False,
                    max_tokens=4096,
                )
                content = response.choices[0].message.content
                if not content:
                    raise IntegrationUnavailable("模型返回了空 JSON。")
                return output_model.model_validate_json(content)
            except Exception as exc:
                last_error = exc
        raise IntegrationUnavailable(f"兼容模型调用失败：{last_error}") from last_error

    def _validate_advice(
        self, advice: Advice, state: GameState, report: SolverReport
    ) -> None:
        valid_positions = set(range(1, state.config.card_count + 1))
        if not set(advice.positions).issubset(valid_positions):
            raise AdviceValidationError("建议引用了不存在的牌位。")
        if advice.action_type == ActionType.WAIT and advice.positions:
            raise AdviceValidationError("等待动作不能引用牌位。")
        legal = {
            (item.action_type, tuple(item.positions)) for item in report.legal_actions
        }
        if advice.action_type != ActionType.WAIT and (
            advice.action_type,
            tuple(advice.positions),
        ) not in legal:
            raise AdviceValidationError("模型建议了非法动作。")
        event_ids = {event.event_id for event in state.events}
        if not set(advice.evidence_event_ids).issubset(event_ids):
            raise AdviceValidationError("建议引用了不存在的证据事件。")

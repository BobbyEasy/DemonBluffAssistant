from __future__ import annotations

from types import SimpleNamespace

import pytest

from demon_bluff_assistant.config import Settings
from demon_bluff_assistant.models import (
    ActionType,
    Advice,
    GameState,
    LegalAction,
    ObservationEvent,
    ObservationKind,
    SolverReport,
    StatePatch,
    SeatState,
    VillageConfig,
    VillageSetupSuggestion,
)
from demon_bluff_assistant.model_config import (
    ModelConfigStore,
    ModelSettingsUpdate,
)
from demon_bluff_assistant.openai_service import (
    AdviceValidationError,
    IntegrationUnavailable,
    OpenAIService,
)
from demon_bluff_assistant.store import SessionStore
from demon_bluff_assistant.store import SessionNotFound


class FakeResponses:
    def __init__(self, parsed_values) -> None:
        self.parsed_values = list(parsed_values)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        value = self.parsed_values.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(output_parsed=value)


class FakeOpenAIClient:
    def __init__(self, parsed_values) -> None:
        self.responses = FakeResponses(parsed_values)


class FakeChatCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeCompatibleClient(FakeOpenAIClient):
    def __init__(self, parsed_values, chat_content: str) -> None:
        super().__init__(parsed_values)
        self.chat = SimpleNamespace(completions=FakeChatCompletions(chat_content))


class FakeProtector:
    def protect(self, secret: str) -> str:
        return "encrypted:" + secret

    def unprotect(self, protected: str) -> str:
        return protected.removeprefix("encrypted:")


def config() -> VillageConfig:
    return VillageConfig(card_count=3, evil_count=1, minion_count=0, demon_count=1)


def test_session_store_persists_patch_and_undoes_whole_snapshot(tmp_path) -> None:
    store = SessionStore(tmp_path)
    state = store.create(config())
    patch = StatePatch(
        seats=[{"position": 2, "visible_role": "Alchemist", "revealed": True}],
        events=[
            ObservationEvent(
                event_id="event-1",
                speaker_position=2,
                role_id="alchemist",
                kind=ObservationKind.FREE_TEXT,
                raw_text="没有腐化",
            )
        ],
        overall_confidence=0.98,
    )

    updated = store.apply_patch(state.session_id, patch)
    reloaded = SessionStore(tmp_path).get(state.session_id)

    assert updated.seats[1].visible_role == "Alchemist"
    assert reloaded.events[0].event_id == "event-1"

    restored = store.undo(state.session_id)
    assert restored.seats[1].visible_role is None
    assert restored.events == []


def test_session_import_keeps_validated_state_and_new_id(tmp_path) -> None:
    store = SessionStore(tmp_path)
    source = GameState(config=config())

    imported = store.import_state(source.model_dump(mode="json"))

    assert imported.session_id != source.session_id
    assert store.get(imported.session_id).config.card_count == 3


def test_parse_capture_uses_responses_api_image_and_strict_model() -> None:
    expected = StatePatch(overall_confidence=0.9)
    client = FakeOpenAIClient([expected])
    service = OpenAIService(
        Settings(openai_api_key="secret", openai_model="gpt-test"), client=client
    )

    result = service.parse_capture(b"png-bytes", GameState(config=config()))

    assert result == expected
    call = client.responses.calls[0]
    assert call["model"] == "gpt-test"
    assert call["text_format"] is StatePatch
    assert call["store"] is False
    image = call["input"][0]["content"][1]
    assert image["type"] == "input_image"
    assert image["image_url"].startswith("data:image/png;base64,")
    assert "secret" not in str(call)


def test_parse_capture_retries_once_then_returns_valid_patch() -> None:
    expected = StatePatch(overall_confidence=0.8)
    client = FakeOpenAIClient([RuntimeError("temporary"), expected])
    service = OpenAIService(Settings(openai_api_key="secret"), client=client)

    assert service.parse_capture(b"png", GameState(config=config())) == expected
    assert len(client.responses.calls) == 2


def test_parse_village_uses_visual_structured_output() -> None:
    expected = VillageSetupSuggestion(
        config=config(), overall_confidence=0.93, warnings=[]
    )
    client = FakeOpenAIClient([expected])
    service = OpenAIService(
        Settings(openai_api_key="secret", openai_model="gpt-test"), client=client
    )

    assert service.parse_village(b"png") == expected
    call = client.responses.calls[0]
    assert call["text_format"] is VillageSetupSuggestion
    assert call["input"][0]["content"][1]["type"] == "input_image"


def test_missing_key_reports_manual_fallback() -> None:
    service = OpenAIService(Settings(openai_api_key=None))

    with pytest.raises(IntegrationUnavailable, match="模型设置"):
        service.parse_capture(b"png", GameState(config=config()))


def test_advice_is_rejected_when_model_invents_an_illegal_action() -> None:
    illegal = Advice(
        action_type=ActionType.EXECUTE,
        positions=[3],
        summary="处决 #3",
        uncertainty="低",
    )
    client = FakeOpenAIClient([illegal])
    service = OpenAIService(Settings(openai_api_key="secret"), client=client)
    report = SolverReport(
        satisfiable=True,
        world_count=1,
        legal_actions=[
            LegalAction(action_type=ActionType.REVEAL, positions=[2], label="翻开 #2")
        ],
    )

    with pytest.raises(AdviceValidationError, match="非法动作"):
        service.generate_advice(GameState(config=config()), report)


def test_wait_advice_cannot_reference_a_position() -> None:
    service = OpenAIService(Settings())
    advice = Advice(
        action_type=ActionType.WAIT,
        positions=[2],
        summary="等待 #2",
        uncertainty="低",
    )

    with pytest.raises(AdviceValidationError, match="等待动作"):
        service._validate_advice(
            advice,
            GameState(config=config()),
            SolverReport(satisfiable=True),
        )


def test_session_store_rejects_oversized_session_id_before_filesystem_access(
    tmp_path,
) -> None:
    store = SessionStore(tmp_path)

    with pytest.raises(SessionNotFound):
        store.get("a" * 300)


def test_unsatisfiable_report_returns_local_wait_advice_without_api_call() -> None:
    client = FakeOpenAIClient([])
    service = OpenAIService(Settings(openai_api_key="secret"), client=client)

    advice = service.generate_advice(
        GameState(config=config()),
        SolverReport(satisfiable=False, conflict_event_ids=["bad"]),
    )

    assert advice.action_type == ActionType.WAIT
    assert advice.evidence_event_ids == ["bad"]
    assert client.responses.calls == []


def test_deepseek_strategy_uses_chat_json_and_keeps_vision_on_openai(tmp_path) -> None:
    store = ModelConfigStore(tmp_path / "models.json", FakeProtector())
    store.update(
        ModelSettingsUpdate(
            provider="openai",
            model="gpt-5.6-terra",
            api_key="test-openai-key",
            activate=False,
        )
    )
    store.update(
        ModelSettingsUpdate(
            provider="deepseek",
            model="deepseek-v4-pro",
            api_key="test-deepseek-key",
        )
    )
    expected = Advice(
        action_type=ActionType.REVEAL,
        positions=[2],
        summary="翻开 #2",
        uncertainty="信息有限",
    )
    client = FakeCompatibleClient([], expected.model_dump_json())
    service = OpenAIService(Settings(), client=client, model_store=store)
    report = SolverReport(
        satisfiable=True,
        legal_actions=[
            LegalAction(action_type=ActionType.REVEAL, positions=[2], label="翻开 #2")
        ],
    )

    advice = service.generate_advice(
        GameState(
            config=config(),
            seats=[SeatState(position=1, visible_role="Fortune Teller")],
        ),
        report,
    )

    assert advice == expected
    call = client.chat.completions.calls[0]
    assert call["model"] == "deepseek-v4-pro"
    assert call["response_format"] == {"type": "json_object"}
    assert "visible_role_rules" in call["messages"][1]["content"]
    assert "fortune_teller" in call["messages"][1]["content"]


def test_zhipu_glm_vision_uses_base64_image_and_validates_state_patch(tmp_path) -> None:
    store = ModelConfigStore(tmp_path / "models.json", FakeProtector())
    store.update(
        ModelSettingsUpdate(
            provider="zhipu",
            model="glm-4.6v-flash",
            api_key="test-zhipu-key",
            activate=False,
        )
    )
    client = FakeCompatibleClient(
        [],
        '```json\n{"seats":[{"position":1,"visible_role":"Architect",'
        '"claim_text":"左边有更多恶徒"}],"events":[],"warnings":[],'
        '"overall_confidence":0.94}\n```',
    )
    service = OpenAIService(Settings(), client=client, model_store=store)

    patch = service.parse_capture_zhipu(b"png-bytes", GameState(config=config()))

    assert patch.seats[0].position == 1
    assert patch.recognition_engine == "glm-4.6v-flash"
    call = client.chat.completions.calls[0]
    assert call["model"] == "glm-4.6v-flash"
    assert "response_format" not in call
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    image = call["messages"][1]["content"][0]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert "test-zhipu-key" not in str(call)


def test_zhipu_glm_vision_rejects_position_outside_village(tmp_path) -> None:
    store = ModelConfigStore(tmp_path / "models.json", FakeProtector())
    store.update(
        ModelSettingsUpdate(
            provider="zhipu",
            model="glm-4.6v-flash",
            api_key="test-zhipu-key",
            activate=False,
        )
    )
    client = FakeCompatibleClient(
        [],
        '{"seats":[{"position":9,"visible_role":"Architect"}],'
        '"overall_confidence":0.9}',
    )
    service = OpenAIService(Settings(), client=client, model_store=store)

    with pytest.raises(IntegrationUnavailable, match="牌位超出"):
        service.parse_capture_zhipu(b"png", GameState(config=config()))


def test_deepseek_strategy_chat_includes_history_solver_and_mechanics(tmp_path) -> None:
    store = ModelConfigStore(tmp_path / "models.json", FakeProtector())
    store.update(
        ModelSettingsUpdate(
            provider="deepseek",
            model="deepseek-v4-pro",
            api_key="test-deepseek-key",
        )
    )
    client = FakeCompatibleClient([], "建议先核对 #2 的证词。")
    service = OpenAIService(Settings(), client=client, model_store=store)
    state = GameState(
        config=VillageConfig(
            card_count=3,
            evil_count=1,
            evil_count_max=2,
            minion_count=0,
            minion_count_max=1,
            demon_count=0,
            demon_count_max=1,
        ),
        seats=[SeatState(position=2, visible_role="Fortune Teller")],
    )
    report = SolverReport(satisfiable=True, world_count=3)

    answer = service.continue_strategy_chat(
        state,
        report,
        [{"role": "user", "content": "上一轮问题"}, {"role": "assistant", "content": "上一轮回答"}],
        "现在优先验证什么？",
    )

    assert answer == "建议先核对 #2 的证词。"
    call = client.chat.completions.calls[0]
    assert call["model"] == "deepseek-v4-pro"
    assert call["messages"][-1]["content"] == "现在优先验证什么？"
    assert call["messages"][-2]["content"] == "上一轮回答"
    context = call["messages"][1]["content"]
    assert "solver_report" in context
    assert "evil_count_max" in context
    assert "腐化" in call["messages"][0]["content"]
    assert "隐藏思维过程" in call["messages"][0]["content"]

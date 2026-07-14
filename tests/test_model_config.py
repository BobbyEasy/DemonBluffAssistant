from __future__ import annotations

import json
import os

import pytest
from pydantic import ValidationError

from demon_bluff_assistant.config import Settings
from demon_bluff_assistant.model_config import (
    ModelConfigStore,
    ModelProvider,
    ModelSettingsUpdate,
    WindowsDPAPIProtector,
)


class FakeProtector:
    def protect(self, secret: str) -> str:
        return "cipher:" + secret[::-1]

    def unprotect(self, protected: str) -> str:
        return protected.removeprefix("cipher:")[::-1]


def test_model_config_encrypts_each_key_and_switches_provider(tmp_path) -> None:
    path = tmp_path / "model-config.json"
    store = ModelConfigStore(path, FakeProtector())

    store.update(
        ModelSettingsUpdate(
            provider=ModelProvider.OPENAI,
            model="gpt-5.6-terra",
            api_key="test-openai-key",
            activate=False,
        )
    )
    store.update(
        ModelSettingsUpdate(
            provider=ModelProvider.DEEPSEEK,
            model="deepseek-v4-pro",
            api_key="test-deepseek-key",
            activate=True,
        )
    )

    raw = path.read_text(encoding="utf-8")
    assert "test-openai-key" not in raw
    assert "test-deepseek-key" not in raw
    assert store.resolve(ModelProvider.OPENAI, Settings()).api_key == "test-openai-key"
    assert store.active(Settings()).model == "deepseek-v4-pro"

    public = store.public_view(Settings()).model_dump(mode="json")
    assert public["active_provider"] == "deepseek"
    assert "api_key" not in json.dumps(public)
    assert next(item for item in public["providers"] if item["provider"] == "openai")[
        "supports_vision"
    ]


def test_blank_api_key_preserves_saved_secret_and_clear_is_explicit(tmp_path) -> None:
    store = ModelConfigStore(tmp_path / "models.json", FakeProtector())
    store.update(
        ModelSettingsUpdate(
            provider="deepseek",
            model="deepseek-v4-pro",
            api_key="test-saved-key",
        )
    )

    store.update(ModelSettingsUpdate(provider="deepseek", model="deepseek-v4-flash"))
    assert store.resolve(ModelProvider.DEEPSEEK, Settings()).api_key == "test-saved-key"

    store.update(
        ModelSettingsUpdate(
            provider="deepseek",
            model="deepseek-v4-flash",
            clear_api_key=True,
        )
    )
    assert store.resolve(ModelProvider.DEEPSEEK, Settings()) is None


def test_zhipu_vision_key_does_not_switch_active_strategy_provider(tmp_path) -> None:
    store = ModelConfigStore(tmp_path / "models.json", FakeProtector())
    store.update(
        ModelSettingsUpdate(
            provider="deepseek",
            model="deepseek-v4-pro",
            api_key="test-deepseek-key",
            activate=True,
        )
    )

    view = store.update(
        ModelSettingsUpdate(
            provider="zhipu",
            model="glm-4.6v-flash",
            api_key="test-zhipu-key",
            activate=False,
        )
    )

    assert view.active_provider == ModelProvider.DEEPSEEK
    profile = store.resolve(ModelProvider.ZHIPU, Settings())
    assert profile is not None
    assert profile.model == "glm-4.6v-flash"
    assert profile.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert profile.api_key == "test-zhipu-key"
    zhipu = next(item for item in view.providers if item.provider == "zhipu")
    assert zhipu.configured is True
    assert zhipu.supports_vision is True


def test_custom_provider_requires_an_http_base_url() -> None:
    with pytest.raises(ValidationError):
        ModelSettingsUpdate(
            provider="custom",
            model="local-model",
            base_url="file:///tmp/model",
            api_key="secret",
        )

    with pytest.raises(ValidationError):
        ModelSettingsUpdate(provider="openai", model="   ", api_key="secret")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://u:p" + "@example.invalid/v1",
        "https://example.com/v1?api_key=secret",
        "https://example.com/v1#secret",
        "http://example.com/v1",
    ],
)
def test_custom_provider_rejects_urls_that_can_leak_credentials(base_url) -> None:
    with pytest.raises(ValidationError):
        ModelSettingsUpdate(
            provider="custom",
            model="model",
            base_url=base_url,
            api_key="secret",
        )


def test_custom_provider_allows_plain_http_only_for_loopback() -> None:
    update = ModelSettingsUpdate(
        provider="custom",
        model="local-model",
        base_url="http://127.0.0.1:11434/v1/",
        api_key="local-secret",
    )

    assert update.base_url == "http://127.0.0.1:11434/v1"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_windows_dpapi_round_trip_does_not_contain_plaintext() -> None:
    protector = WindowsDPAPIProtector()

    encrypted = protector.protect("test-local-key")

    assert "test-local-key" not in encrypted
    assert protector.unprotect(encrypted) == "test-local-key"

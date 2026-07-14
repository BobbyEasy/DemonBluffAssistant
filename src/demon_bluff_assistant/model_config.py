from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from demon_bluff_assistant.config import Settings


class ModelConfigError(RuntimeError):
    pass


class ModelProvider(StrEnum):
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    ZHIPU = "zhipu"
    CUSTOM = "custom"


PROVIDER_DEFAULTS = {
    ModelProvider.OPENAI: {
        "label": "OpenAI",
        "model": "gpt-5.6-terra",
        "base_url": "https://api.openai.com/v1",
        "known_models": ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"],
        "supports_vision": True,
    },
    ModelProvider.DEEPSEEK: {
        "label": "DeepSeek",
        "model": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com",
        "known_models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "supports_vision": False,
    },
    ModelProvider.ZHIPU: {
        "label": "智谱 GLM",
        "model": "glm-4.6v-flash",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "known_models": ["glm-4.6v-flash"],
        "supports_vision": True,
    },
    ModelProvider.CUSTOM: {
        "label": "兼容接口",
        "model": "",
        "base_url": "",
        "known_models": [],
        "supports_vision": False,
    },
}


class ModelSettingsUpdate(BaseModel):
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, repr=False, max_length=4096)
    base_url: str | None = Field(default=None, max_length=500)
    activate: bool = True
    clear_api_key: bool = False

    @model_validator(mode="after")
    def validate_and_normalize(self) -> "ModelSettingsUpdate":
        self.model = self.model.strip()
        if not self.model:
            raise ValueError("模型 ID 不能为空。")
        if self.api_key is not None:
            self.api_key = self.api_key.strip() or None
        if self.provider == ModelProvider.CUSTOM:
            if not self.base_url:
                raise ValueError("自定义兼容接口必须填写 Base URL。")
            self.base_url = self.base_url.strip()
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Base URL 必须是有效的 http(s) 地址。")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Base URL 不得包含凭据、查询参数或片段。")
            if parsed.scheme == "http" and parsed.hostname not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise ValueError("公网兼容接口必须使用 HTTPS。")
            self.base_url = self.base_url.rstrip("/")
        else:
            self.base_url = str(PROVIDER_DEFAULTS[self.provider]["base_url"])
        return self


class ProviderStatus(BaseModel):
    provider: ModelProvider
    label: str
    model: str
    base_url: str
    configured: bool
    supports_vision: bool
    known_models: list[str] = Field(default_factory=list)


class ModelSettingsView(BaseModel):
    active_provider: ModelProvider
    providers: list[ProviderStatus]


@dataclass(frozen=True)
class ResolvedProfile:
    provider: ModelProvider
    model: str
    base_url: str
    api_key: str = field(repr=False)


class SecretProtector(Protocol):
    def protect(self, secret: str) -> str: ...

    def unprotect(self, protected: str) -> str: ...


class WindowsDPAPIProtector:
    """Encrypt secrets for the current Windows user account."""

    description = "Demon Bluff Assistant model credential"

    def protect(self, secret: str) -> str:
        if os.name != "nt":
            raise ModelConfigError("DPAPI 仅在 Windows 上可用。")
        import win32crypt

        encrypted = win32crypt.CryptProtectData(
            secret.encode("utf-8"), self.description, None, None, None, 0
        )
        return base64.b64encode(encrypted).decode("ascii")

    def unprotect(self, protected: str) -> str:
        if os.name != "nt":
            raise ModelConfigError("DPAPI 仅在 Windows 上可用。")
        import win32crypt

        try:
            encrypted = base64.b64decode(protected.encode("ascii"), validate=True)
            _, plaintext = win32crypt.CryptUnprotectData(
                encrypted, None, None, None, 0
            )
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise ModelConfigError("无法解密模型凭据；请重新填写 API Key。") from exc


class ModelConfigStore:
    def __init__(self, path: Path, protector: SecretProtector) -> None:
        self.path = Path(path)
        self.protector = protector

    def update(self, update: ModelSettingsUpdate) -> ModelSettingsView:
        data = self._load()
        providers = data.setdefault("providers", {})
        current = dict(providers.get(update.provider.value, {}))

        if update.clear_api_key:
            encrypted_key = None
        elif update.api_key:
            encrypted_key = self.protector.protect(update.api_key)
        else:
            encrypted_key = current.get("encrypted_key")

        current.update(
            {
                "model": update.model,
                "base_url": update.base_url,
                "encrypted_key": encrypted_key,
            }
        )
        providers[update.provider.value] = current
        if update.activate:
            data["active_provider"] = update.provider.value
        self._save(data)
        return self.public_view(Settings())

    def resolve(
        self, provider: ModelProvider, fallback: Settings
    ) -> ResolvedProfile | None:
        data = self._load()
        saved = data.get("providers", {}).get(provider.value, {})
        defaults = PROVIDER_DEFAULTS[provider]
        encrypted = saved.get("encrypted_key")
        if encrypted:
            secret = self.protector.unprotect(encrypted)
        elif provider == ModelProvider.OPENAI and fallback.openai_api_key:
            secret = fallback.openai_api_key
        else:
            return None
        model = saved.get("model") or (
            fallback.openai_model
            if provider == ModelProvider.OPENAI
            else defaults["model"]
        )
        base_url = saved.get("base_url") or defaults["base_url"]
        return ResolvedProfile(
            provider=provider,
            model=str(model),
            base_url=str(base_url),
            api_key=secret,
        )

    def active(self, fallback: Settings) -> ResolvedProfile | None:
        data = self._load()
        try:
            provider = ModelProvider(data.get("active_provider", "openai"))
        except ValueError:
            provider = ModelProvider.OPENAI
        return self.resolve(provider, fallback)

    def public_view(self, fallback: Settings) -> ModelSettingsView:
        data = self._load()
        try:
            active = ModelProvider(data.get("active_provider", "openai"))
        except ValueError:
            active = ModelProvider.OPENAI
        saved_providers = data.get("providers", {})
        providers = []
        for provider, defaults in PROVIDER_DEFAULTS.items():
            saved = saved_providers.get(provider.value, {})
            model = saved.get("model") or (
                fallback.openai_model
                if provider == ModelProvider.OPENAI
                else defaults["model"]
            )
            base_url = saved.get("base_url") or defaults["base_url"]
            providers.append(
                ProviderStatus(
                    provider=provider,
                    label=str(defaults["label"]),
                    model=str(model),
                    base_url=str(base_url),
                    configured=self.resolve(provider, fallback) is not None,
                    supports_vision=bool(defaults["supports_vision"]),
                    known_models=list(defaults["known_models"]),
                )
            )
        return ModelSettingsView(active_provider=active, providers=providers)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "active_provider": "openai", "providers": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelConfigError("模型配置文件损坏，无法读取。") from exc
        if not isinstance(payload, dict):
            raise ModelConfigError("模型配置文件格式不正确。")
        return payload

    def _save(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

from __future__ import annotations

from fastapi.testclient import TestClient

from demon_bluff_assistant.api import create_app
from demon_bluff_assistant.capture import CaptureRegistry
from demon_bluff_assistant.config import Settings
from demon_bluff_assistant.model_config import ModelConfigStore
from demon_bluff_assistant.models import Advice, StatePatch, VillageSetupSuggestion
from demon_bluff_assistant.openai_service import AdviceValidationError, OpenAIService
from demon_bluff_assistant.solver import WorldSolver
from demon_bluff_assistant.store import SessionStore


class FakeCaptures:
    def __init__(self) -> None:
        self.registry = CaptureRegistry()
        self.capture_id = self.registry.add(b"fake-png")

    def capture_now(self):
        return {
            "capture_id": self.capture_id,
            "status": "ready",
            "message": "截图已就绪",
        }

    def latest(self):
        return self.capture_now()


class FakeAI(OpenAIService):
    def __init__(self) -> None:
        self.settings = Settings(openai_api_key="fake", openai_model="fake-model")
        self.client = None

    def parse_capture(self, png_bytes, state):
        assert png_bytes == b"fake-png"
        return StatePatch(
            seats=[{"position": 2, "visible_role": "Alchemist", "revealed": True}],
            overall_confidence=0.97,
        )

    def parse_village(self, png_bytes):
        assert png_bytes == b"fake-png"
        return VillageSetupSuggestion(
            config={
                "card_count": 4,
                "evil_count": 1,
                "minion_count": 0,
                "demon_count": 1,
                "health": 9,
            },
            overall_confidence=0.96,
        )

    def parse_capture_zhipu(self, png_bytes, state):
        assert png_bytes == b"fake-png"
        return StatePatch(
            seats=[{"position": 1, "visible_role": "Architect"}],
            overall_confidence=0.95,
            recognition_engine="glm-4.6v-flash",
        )

    def parse_village_zhipu(self, png_bytes):
        assert png_bytes == b"fake-png"
        return VillageSetupSuggestion(
            config={
                "card_count": 4,
                "evil_count": 1,
                "minion_count": 0,
                "demon_count": 1,
                "health": 9,
            },
            overall_confidence=0.95,
            recognition_engine="glm-4.6v-flash",
        )

    def generate_advice(self, state, report):
        return Advice(
            action_type="wait",
            summary="继续取证",
            uncertainty="信息不足",
        )


class FakeLocalVision:
    def parse_capture(self, png_bytes, state):
        assert png_bytes == b"fake-png"
        return StatePatch(
            seats=[{"position": 2, "visible_role": "Alchemist", "revealed": True}],
            overall_confidence=0.99,
            recognition_engine="rapidocr-local",
        )

    def parse_village(self, png_bytes):
        assert png_bytes == b"fake-png"
        return VillageSetupSuggestion(
            config={
                "card_count": 4,
                "evil_count": 1,
                "minion_count": 0,
                "demon_count": 1,
                "health": 9,
            },
            overall_confidence=0.98,
            recognition_engine="rapidocr-local",
        )


class FakeProtector:
    def protect(self, secret: str) -> str:
        return "cipher:" + secret

    def unprotect(self, protected: str) -> str:
        return protected.removeprefix("cipher:")


def client(tmp_path, openai_service=None) -> TestClient:
    settings = Settings(openai_api_key=None, data_dir=tmp_path)
    app = create_app(
        settings=settings,
        store=SessionStore(tmp_path),
        solver=WorldSolver(),
        openai_service=openai_service or FakeAI(),
        local_vision=FakeLocalVision(),
        model_store=ModelConfigStore(tmp_path / "models.json", FakeProtector()),
        captures=FakeCaptures(),
        serve_static=False,
    )
    return TestClient(app)


def test_session_capture_confirmation_analysis_and_undo(tmp_path) -> None:
    api = client(tmp_path)
    created = api.post(
        "/api/sessions",
        json={
            "card_count": 3,
            "evil_count": 1,
            "minion_count": 0,
            "demon_count": 1,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert len(created.json()["seats"]) == 3

    captured = api.post("/api/captures")
    capture_id = captured.json()["capture_id"]
    parsed = api.post(
        f"/api/captures/{capture_id}/parse", params={"session_id": session_id}
    )
    assert parsed.status_code == 200
    assert parsed.json()["overall_confidence"] == 0.99
    assert parsed.json()["recognition_engine"] == "rapidocr-local"

    confirmed = api.post(
        f"/api/sessions/{session_id}/events", json=parsed.json()
    )
    assert confirmed.json()["seats"][1]["visible_role"] == "Alchemist"

    analysis = api.get(f"/api/sessions/{session_id}/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["report"]["satisfiable"] is True
    assert analysis.json()["advice"]["summary"] == "继续取证"

    undone = api.post(f"/api/sessions/{session_id}/undo")
    assert undone.json()["seats"][1]["visible_role"] is None


def test_config_never_returns_key_and_unknown_capture_is_404(tmp_path) -> None:
    api = client(tmp_path)

    config = api.get("/api/config")
    missing = api.post(
        "/api/captures/not-found/parse", params={"session_id": "not-found"}
    )

    assert config.json()["openai_configured"] is False
    assert "api_key" not in str(config.json()).lower()
    assert missing.status_code == 404


def test_export_and_import_create_a_new_session(tmp_path) -> None:
    api = client(tmp_path)
    created = api.post(
        "/api/sessions",
        json={
            "card_count": 4,
            "evil_count": 1,
            "minion_count": 0,
            "demon_count": 1,
        },
    ).json()

    exported = api.get(f"/api/sessions/{created['session_id']}/export").json()
    imported = api.post("/api/sessions/import", json=exported)

    assert imported.status_code == 201
    assert imported.json()["session_id"] != created["session_id"]
    assert imported.json()["config"]["card_count"] == 4


def test_model_settings_never_return_key_and_can_switch_to_deepseek(tmp_path) -> None:
    api = client(tmp_path)

    saved = api.put(
        "/api/model-settings",
        json={
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "api_key": "test-user-key",
            "activate": True,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["active_provider"] == "deepseek"
    assert "test-user-key" not in saved.text
    assert "api_key" not in saved.text


def test_capture_can_detect_village_before_a_session_exists(tmp_path) -> None:
    api = client(tmp_path)
    capture_id = api.post("/api/captures").json()["capture_id"]

    detected = api.post(f"/api/captures/{capture_id}/village")

    assert detected.status_code == 200
    assert detected.json()["config"]["card_count"] == 4
    assert detected.json()["config"]["health"] == 9
    assert detected.json()["recognition_engine"] == "rapidocr-local"


def test_capture_can_use_zhipu_glm_as_optional_vision_engine(tmp_path) -> None:
    api = client(tmp_path)
    capture_id = api.post("/api/captures").json()["capture_id"]
    state = api.post(
        "/api/sessions",
        json={
            "card_count": 4,
            "evil_count": 1,
            "minion_count": 0,
            "demon_count": 1,
        },
    ).json()

    parsed = api.post(
        f"/api/captures/{capture_id}/parse",
        params={"session_id": state["session_id"], "engine": "glm"},
    )
    village = api.post(
        f"/api/captures/{capture_id}/village", params={"engine": "glm"}
    )

    assert parsed.status_code == 200
    assert parsed.json()["recognition_engine"] == "glm-4.6v-flash"
    assert village.status_code == 200
    assert village.json()["recognition_engine"] == "glm-4.6v-flash"


def test_capture_image_is_not_cached_and_untrusted_host_is_rejected(tmp_path) -> None:
    api = client(tmp_path)
    capture_id = api.post("/api/captures").json()["capture_id"]

    image = api.get(f"/api/captures/{capture_id}/image")
    untrusted = api.get("/api/config", headers={"host": "malicious.example"})

    assert image.headers["cache-control"] == "no-store"
    assert untrusted.status_code == 400


class InvalidAdviceAI(FakeAI):
    def generate_advice(self, state, report):
        raise AdviceValidationError("模型建议了非法动作。")

    def template_advice(self, report):
        return Advice(
            action_type="wait",
            summary="已安全回退到本地建议",
            uncertainty="模型输出无效",
        )


def test_invalid_model_advice_falls_back_instead_of_crashing_analysis(tmp_path) -> None:
    api = client(tmp_path, InvalidAdviceAI())
    state = api.post(
        "/api/sessions",
        json={
            "card_count": 3,
            "evil_count": 1,
            "minion_count": 0,
            "demon_count": 1,
        },
    ).json()

    analysis = api.get(f"/api/sessions/{state['session_id']}/analysis")

    assert analysis.status_code == 200
    assert analysis.json()["advice"]["summary"] == "已安全回退到本地建议"

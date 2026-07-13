from __future__ import annotations

from io import BytesIO
import os
from ctypes import wintypes

import pytest
from PIL import Image

from demon_bluff_assistant.capture import (
    CaptureError,
    CaptureRegistry,
    CaptureService,
    Rect,
    Win32CaptureBackend,
)
from demon_bluff_assistant.config import Settings


class FakeCaptureBackend:
    def __init__(self, process_name: str = "Demon Bluff.exe") -> None:
        self.process_name_value = process_name
        self.grabbed_rect: Rect | None = None

    def foreground_window(self) -> int:
        return 42

    def process_name(self, hwnd: int) -> str:
        assert hwnd == 42
        return self.process_name_value

    def client_rect_screen(self, hwnd: int) -> Rect:
        assert hwnd == 42
        return Rect(left=100, top=200, right=900, bottom=700)

    def grab_png(self, rect: Rect) -> bytes:
        self.grabbed_rect = rect
        image = Image.new("RGB", (rect.width, rect.height), "#112233")
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def test_settings_do_not_expose_api_key_in_public_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")

    settings = Settings.from_env()

    assert settings.openai_api_key == "secret-value"
    assert settings.openai_model == "gpt-test"
    assert settings.public_dict() == {
        "model": "gpt-test",
        "openai_configured": True,
        "debug_save_screenshots": False,
        "hotkey": "Ctrl+Shift+D",
    }
    assert "secret-value" not in repr(settings)


def test_capture_only_accepts_demon_bluff_foreground_window() -> None:
    backend = FakeCaptureBackend()
    service = CaptureService(backend=backend)

    captured = service.capture_foreground_game()

    assert captured.process_name == "Demon Bluff.exe"
    assert captured.rect == Rect(100, 200, 900, 700)
    assert backend.grabbed_rect == captured.rect
    with Image.open(BytesIO(captured.png_bytes)) as image:
        assert image.size == (800, 500)


def test_capture_rejects_another_foreground_process() -> None:
    service = CaptureService(backend=FakeCaptureBackend("chrome.exe"))

    with pytest.raises(CaptureError, match="请先切换到 Demon Bluff"):
        service.capture_foreground_game()


def test_capture_registry_is_memory_only_and_bounded() -> None:
    registry = CaptureRegistry(max_items=2)

    first = registry.add(b"one")
    second = registry.add(b"two")
    third = registry.add(b"three")

    assert registry.get(first) is None
    assert registry.get(second) == b"two"
    assert registry.get(third) == b"three"


@pytest.mark.skipif(os.name != "nt", reason="Windows API only")
def test_win32_capture_backend_declares_pointer_sized_process_handles() -> None:
    backend = Win32CaptureBackend()

    assert backend.kernel32.OpenProcess.restype is wintypes.HANDLE

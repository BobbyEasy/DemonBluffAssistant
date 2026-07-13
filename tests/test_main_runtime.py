from __future__ import annotations

import socket

from demon_bluff_assistant.config import Settings
from demon_bluff_assistant.main import (
    _browser_autostart_enabled,
    _port_is_available,
    build_runtime,
)


def test_runtime_builds_local_app_without_starting_hotkey(tmp_path) -> None:
    runtime = build_runtime(Settings(data_dir=tmp_path, openai_api_key=None))

    assert runtime.app.title == "Demon Bluff Assistant"
    assert runtime.hotkey is not None
    assert runtime.captures.latest()["status"] == "idle"


def test_developer_start_script_runs_module() -> None:
    content = open("start.ps1", encoding="utf-8").read()

    assert "demon_bluff_assistant.main" in content
    assert "OPENAI_API_KEY" not in content


def test_build_script_fails_when_pyinstaller_fails_or_output_is_running() -> None:
    content = open("build.ps1", encoding="utf-8").read()

    assert "$LASTEXITCODE" in content
    assert "Get-Process" in content
    assert "throw" in content
    assert "StopDemonBluffAssistant" in content


def test_browser_autostart_can_be_disabled_for_headless_smoke_tests(monkeypatch) -> None:
    monkeypatch.setenv("DEMON_BLUFF_NO_BROWSER", "1")
    assert _browser_autostart_enabled() is False

    monkeypatch.setenv("DEMON_BLUFF_NO_BROWSER", "false")
    assert _browser_autostart_enabled() is True


def test_port_check_detects_an_existing_assistant() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]

        assert _port_is_available("127.0.0.1", port) is False

    assert _port_is_available("127.0.0.1", port) is True

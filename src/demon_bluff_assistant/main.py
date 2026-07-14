from __future__ import annotations

import os
import socket
import threading
import webbrowser
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

from demon_bluff_assistant.api import create_app
from demon_bluff_assistant.analysis_archive import AnalysisArchive
from demon_bluff_assistant.capture import (
    CaptureCoordinator,
    CaptureRegistry,
    CaptureService,
    Win32CaptureBackend,
)
from demon_bluff_assistant.config import Settings
from demon_bluff_assistant.hotkey import GlobalCaptureHotkey, HotkeyError
from demon_bluff_assistant.local_vision import LocalVisionService
from demon_bluff_assistant.model_config import ModelConfigStore, WindowsDPAPIProtector
from demon_bluff_assistant.openai_service import OpenAIService
from demon_bluff_assistant.solver import WorldSolver
from demon_bluff_assistant.store import SessionStore


@dataclass
class Runtime:
    app: FastAPI
    captures: CaptureCoordinator
    hotkey: GlobalCaptureHotkey
    model_store: ModelConfigStore


def _browser_autostart_enabled() -> bool:
    value = os.getenv("DEMON_BLUFF_NO_BROWSER", "")
    return value.strip().lower() not in {"1", "true", "yes", "on"}


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _show_port_conflict(port: int) -> None:
    message = (
        f"端口 {port} 已被占用，可能已有旧版助手正在运行。\n\n"
        "请双击 StopDemonBluffAssistant.exe 后重新启动。"
    )
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Demon Bluff Assistant", 0x30)
    except Exception:
        print(message)


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or Settings.from_env()
    store = SessionStore(settings.data_dir)
    model_store = ModelConfigStore(
        settings.data_dir / "model-config.json", WindowsDPAPIProtector()
    )
    solver = WorldSolver(world_limit=settings.solver_world_limit)
    openai_service = OpenAIService(settings, model_store=model_store)
    local_vision = LocalVisionService()
    debug_dir = (
        settings.data_dir / "debug-screenshots"
        if settings.debug_save_screenshots
        else None
    )
    captures = CaptureCoordinator(
        service=CaptureService(Win32CaptureBackend()),
        registry=CaptureRegistry(max_items=5),
        debug_dir=debug_dir,
    )
    hotkey = GlobalCaptureHotkey(captures.capture_now)
    app = create_app(
        settings=settings,
        store=store,
        solver=solver,
        openai_service=openai_service,
        local_vision=local_vision,
        model_store=model_store,
        captures=captures,
        analysis_archive=AnalysisArchive(settings.data_dir / "analysis.db"),
    )
    return Runtime(
        app=app, captures=captures, hotkey=hotkey, model_store=model_store
    )


def main() -> None:
    settings = Settings.from_env()
    host = "127.0.0.1"
    port = int(os.getenv("DEMON_BLUFF_PORT", "8765"))
    if not _port_is_available(host, port):
        _show_port_conflict(port)
        return

    runtime = build_runtime(settings)
    try:
        runtime.hotkey.start()
    except HotkeyError as exc:
        runtime.captures.record_error(str(exc))

    url = f"http://{host}:{port}"
    if _browser_autostart_enabled():
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(
            runtime.app,
            host=host,
            port=port,
            log_config=None,
            access_log=False,
        )
    finally:
        runtime.hotkey.stop()


if __name__ == "__main__":
    main()

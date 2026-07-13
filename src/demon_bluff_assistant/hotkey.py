from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Callable


class HotkeyError(RuntimeError):
    pass


class GlobalCaptureHotkey:
    HOTKEY_ID = 0xDBA
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(self, callback: Callable[[], object]) -> None:
        if os.name != "nt":
            raise HotkeyError("全局快捷键仅支持 Windows。")
        self.callback = callback
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._started = threading.Event()
        self._startup_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="capture-hotkey")
        self._thread.start()
        self._started.wait(timeout=3)
        if self._startup_error:
            raise HotkeyError(self._startup_error)
        if not self._started.is_set():
            raise HotkeyError("注册全局快捷键超时。")

    def stop(self) -> None:
        if self._thread_id is not None:
            self.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        self._thread_id = self.kernel32.GetCurrentThreadId()
        registered = self.user32.RegisterHotKey(
            None,
            self.HOTKEY_ID,
            self.MOD_CONTROL | self.MOD_SHIFT,
            ord("D"),
        )
        if not registered:
            self._startup_error = "Ctrl+Shift+D 已被其他程序占用。"
            self._started.set()
            return
        self._started.set()
        message = wintypes.MSG()
        try:
            while self.user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == self.WM_HOTKEY and message.wParam == self.HOTKEY_ID:
                    try:
                        self.callback()
                    except Exception:
                        # CaptureCoordinator records the user-facing error.
                        pass
        finally:
            self.user32.UnregisterHotKey(None, self.HOTKEY_ID)


from __future__ import annotations

import ctypes
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol
from uuid import uuid4
from ctypes import wintypes

from PIL import ImageGrab


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class CapturedImage:
    png_bytes: bytes
    process_name: str
    rect: Rect


class CaptureBackend(Protocol):
    def foreground_window(self) -> int: ...

    def process_name(self, hwnd: int) -> str: ...

    def client_rect_screen(self, hwnd: int) -> Rect: ...

    def grab_png(self, rect: Rect) -> bytes: ...


class CaptureService:
    def __init__(self, backend: CaptureBackend) -> None:
        self.backend = backend

    def capture_foreground_game(self) -> CapturedImage:
        hwnd = self.backend.foreground_window()
        if not hwnd:
            raise CaptureError("没有检测到前台窗口。请先切换到 Demon Bluff。")
        process_name = self.backend.process_name(hwnd)
        if process_name.casefold() != "demon bluff.exe":
            raise CaptureError("请先切换到 Demon Bluff，再按 Ctrl+Shift+D。")
        rect = self.backend.client_rect_screen(hwnd)
        if rect.width <= 0 or rect.height <= 0:
            raise CaptureError("游戏窗口已最小化或尺寸无效。")
        return CapturedImage(
            png_bytes=self.backend.grab_png(rect),
            process_name=process_name,
            rect=rect,
        )


class CaptureRegistry:
    def __init__(self, max_items: int = 5) -> None:
        if max_items < 1:
            raise ValueError("max_items must be positive")
        self.max_items = max_items
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.RLock()

    def add(self, png_bytes: bytes) -> str:
        with self._lock:
            capture_id = uuid4().hex
            self._items[capture_id] = png_bytes
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)
            return capture_id

    def get(self, capture_id: str) -> bytes | None:
        with self._lock:
            return self._items.get(capture_id)


class CaptureCoordinator:
    def __init__(
        self,
        service: CaptureService,
        registry: CaptureRegistry,
        debug_dir: Path | None = None,
    ) -> None:
        self.service = service
        self.registry = registry
        self.debug_dir = debug_dir
        self._latest: dict[str, str | None] = {
            "capture_id": None,
            "status": "idle",
            "message": "等待在游戏中按 Ctrl+Shift+D",
        }
        self._lock = threading.Lock()

    def capture_now(self) -> dict[str, str | None]:
        try:
            captured = self.service.capture_foreground_game()
            capture_id = self.registry.add(captured.png_bytes)
            if self.debug_dir is not None:
                self.debug_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                (self.debug_dir / f"{stamp}-{capture_id}.png").write_bytes(
                    captured.png_bytes
                )
            status = {
                "capture_id": capture_id,
                "status": "ready",
                "message": "截图已就绪，请返回伴侣页确认。",
            }
        except CaptureError as exc:
            status = {
                "capture_id": None,
                "status": "error",
                "message": str(exc),
            }
            with self._lock:
                self._latest = status
            raise
        with self._lock:
            self._latest = status
        return status

    def latest(self) -> dict[str, str | None]:
        with self._lock:
            return dict(self._latest)

    def record_error(self, message: str) -> None:
        with self._lock:
            self._latest = {
                "capture_id": None,
                "status": "error",
                "message": message,
            }


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class Win32CaptureBackend:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        if os.name != "nt":
            raise CaptureError("窗口捕获仅支持 Windows。")
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetClientRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_RECT),
        ]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_POINT),
        ]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def foreground_window(self) -> int:
        return int(self.user32.GetForegroundWindow())

    def process_name(self, hwnd: int) -> str:
        pid = ctypes.c_ulong()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel32.OpenProcess(
            self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not handle:
            raise CaptureError("无法读取前台窗口所属进程。")
        try:
            size = ctypes.c_ulong(32_768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = self.kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            )
            if not ok:
                raise CaptureError("无法读取前台窗口所属进程。")
            return os.path.basename(buffer.value)
        finally:
            self.kernel32.CloseHandle(handle)

    def client_rect_screen(self, hwnd: int) -> Rect:
        client = _RECT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(client)):
            raise CaptureError("无法读取游戏窗口尺寸。")
        top_left = _POINT(client.left, client.top)
        bottom_right = _POINT(client.right, client.bottom)
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
            raise CaptureError("无法读取游戏窗口位置。")
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
            raise CaptureError("无法读取游戏窗口位置。")
        return Rect(top_left.x, top_left.y, bottom_right.x, bottom_right.y)

    def grab_png(self, rect: Rect) -> bytes:
        image = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True
        )
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

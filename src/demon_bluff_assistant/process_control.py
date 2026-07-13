from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, field
from typing import Protocol
from ctypes import wintypes


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str


@dataclass(frozen=True)
class CloseReport:
    matched: int = 0
    terminated: int = 0
    failed_pids: list[int] = field(default_factory=list)


class ProcessController(Protocol):
    def list_processes(self) -> list[ProcessInfo]: ...

    def terminate(self, pid: int) -> bool: ...


def is_assistant_process(name: str) -> bool:
    normalized = name.casefold()
    return normalized == "demonbluffassistant.exe" or (
        normalized.startswith("demonbluffassistant-")
        and normalized.endswith(".exe")
    )


def close_assistant_processes(
    controller: ProcessController, current_pid: int | None = None
) -> CloseReport:
    current_pid = current_pid if current_pid is not None else os.getpid()
    targets = [
        process
        for process in controller.list_processes()
        if process.pid != current_pid and is_assistant_process(process.name)
    ]
    terminated = 0
    failed = []
    for process in targets:
        if controller.terminate(process.pid):
            terminated += 1
        else:
            failed.append(process.pid)
    return CloseReport(
        matched=len(targets), terminated=terminated, failed_pids=failed
    )


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class Win32ProcessController:
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT_MS = 3_000

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("进程关闭工具仅支持 Windows。")
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        self.kernel32.Process32FirstW.restype = wintypes.BOOL
        self.kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        self.kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel32.TerminateProcess.restype = wintypes.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def list_processes(self) -> list[ProcessInfo]:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(
            self.TH32CS_SNAPPROCESS, 0
        )
        if snapshot == ctypes.c_void_p(-1).value:
            return []
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        processes = []
        try:
            ok = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                processes.append(ProcessInfo(int(entry.th32ProcessID), entry.szExeFile))
                ok = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel32.CloseHandle(snapshot)
        return processes

    def terminate(self, pid: int) -> bool:
        handle = self.kernel32.OpenProcess(
            self.PROCESS_TERMINATE | self.SYNCHRONIZE, False, pid
        )
        if not handle:
            return False
        try:
            if not self.kernel32.TerminateProcess(handle, 1):
                return False
            self.kernel32.WaitForSingleObject(handle, self.WAIT_TIMEOUT_MS)
            return True
        finally:
            self.kernel32.CloseHandle(handle)

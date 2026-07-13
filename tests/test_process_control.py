from __future__ import annotations

from demon_bluff_assistant.process_control import (
    ProcessInfo,
    close_assistant_processes,
    is_assistant_process,
)


class FakeController:
    def __init__(self) -> None:
        self.processes = [
            ProcessInfo(10, "DemonBluffAssistant.exe"),
            ProcessInfo(11, "DemonBluffAssistant-v0.2.0.exe"),
            ProcessInfo(12, "StopDemonBluffAssistant.exe"),
            ProcessInfo(13, "Demon Bluff.exe"),
        ]
        self.terminated = []

    def list_processes(self):
        return self.processes

    def terminate(self, pid: int) -> bool:
        self.terminated.append(pid)
        return pid != 11


def test_assistant_process_matcher_is_narrow() -> None:
    assert is_assistant_process("DemonBluffAssistant.exe")
    assert is_assistant_process("demonbluffassistant-v0.3.0.EXE")
    assert not is_assistant_process("StopDemonBluffAssistant.exe")
    assert not is_assistant_process("Demon Bluff.exe")
    assert not is_assistant_process("DemonBluffAssistant-backup.txt")


def test_close_tool_terminates_old_and_versioned_assistants_only() -> None:
    controller = FakeController()

    report = close_assistant_processes(controller, current_pid=99)

    assert report.matched == 2
    assert report.terminated == 1
    assert report.failed_pids == [11]
    assert controller.terminated == [10, 11]

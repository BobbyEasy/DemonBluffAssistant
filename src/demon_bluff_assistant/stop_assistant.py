from __future__ import annotations

import ctypes

from demon_bluff_assistant.process_control import (
    Win32ProcessController,
    close_assistant_processes,
)


def main() -> None:
    report = close_assistant_processes(Win32ProcessController())
    if report.matched == 0:
        message = "没有发现正在运行的 Demon Bluff Assistant。"
        icon = 0x40
    elif report.failed_pids:
        message = (
            f"已强制关闭 {report.terminated} 个进程。\n"
            f"无法关闭的 PID：{', '.join(map(str, report.failed_pids))}\n"
            "如仍无法退出，请以管理员身份运行本工具。"
        )
        icon = 0x30
    else:
        message = f"已强制关闭 Demon Bluff Assistant（{report.terminated} 个进程）。"
        icon = 0x40
    ctypes.windll.user32.MessageBoxW(None, message, "Demon Bluff Assistant", icon)


if __name__ == "__main__":
    main()

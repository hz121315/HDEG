# -*- coding: utf-8 -*-
"""每作品一个最小化进度窗口（纯显示，不承载翻译）。

翻译始终在主进程的线程池里完成；这里只负责给每个正在翻译的作品开一个
后台最小化的控制台窗口，实时显示该作品的音轨进度，便于批量运行时观察。

实现方式：主进程把进度写入临时文件，用系统自带的 PowerShell 窗口 tail 该文件。
不引入 GUI 依赖（tkinter 已被打包脚本排除），窗口异常不影响翻译主流程。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

_IS_WINDOWS = sys.platform.startswith('win')


def _safe_name(text: str) -> str:
    """文件名安全化（作品名可能含中文、空格与符号）。"""
    bad = '<>:"/\\|?*'
    out = ''.join('_' if ch in bad else ch for ch in str(text))
    return (out or 'work')[:80]


class WorkProgressWindow:
    """单个作品的进度窗口。"""

    def __init__(self, work_key: str, label: str, progress_dir: Path) -> None:
        self.work_key = work_key
        self.label = label
        self._file = progress_dir / f'{_safe_name(label)}_{abs(hash(work_key)) & 0xFFFFFF:x}.txt'
        self._proc: subprocess.Popen | None = None
        self._closed = False

    def open(self) -> None:
        if not _IS_WINDOWS:
            return
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, 'w', encoding='utf-8') as f:
                f.write(f'HDEG 翻译 - {self.label}\n')
                f.write('-' * 40 + '\n')
            script = (
                f"$Host.UI.RawUI.WindowTitle = 'HDEG 翻译 - {self.label}'; "
                f"Get-Content -LiteralPath '{self._file}' -Encoding UTF8 -Wait"
            )
            self._proc = subprocess.Popen(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-WindowStyle', 'Minimized', '-Command', script],
                creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            # 窗口只是观感，失败不影响翻译
            self._proc = None

    def write(self, line: str) -> None:
        if self._closed:
            return
        try:
            with open(self._file, 'a', encoding='utf-8') as f:
                f.write(str(line).rstrip('\n') + '\n')
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        self._proc = None
        if proc is None or not _IS_WINDOWS:
            return
        try:
            if proc.poll() is None:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5)
        except Exception:
            pass


class WorkWindowManager:
    """按作品开关进度窗口；受 app.work_progress_windows 控制。"""

    def __init__(self, enabled: bool = True, progress_dir: Path | None = None) -> None:
        self.enabled = bool(enabled) and _IS_WINDOWS
        self._dir = progress_dir or (Path(tempfile.gettempdir()) / 'hdeg_progress')
        self._windows: dict[str, WorkProgressWindow] = {}

    def open(self, work_key: str, label: str) -> None:
        if not self.enabled or work_key in self._windows:
            return
        win = WorkProgressWindow(work_key, label, self._dir)
        win.open()
        self._windows[work_key] = win

    def write(self, work_key: str, line: str) -> None:
        if not self.enabled:
            return
        win = self._windows.get(work_key)
        if win is not None:
            win.write(line)

    def close(self, work_key: str) -> None:
        win = self._windows.pop(work_key, None)
        if win is not None:
            win.close()

    def close_all(self) -> None:
        for key in list(self._windows):
            self.close(key)

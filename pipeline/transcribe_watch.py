# -*- coding: utf-8 -*-
"""转录看护：文件稳定判定、实时归档、按作品组就绪通知。

不依赖 orchestrator（不打印、不读配置），便于单元测试。

设计要点：
- infer.exe 写字幕时无法查询"这个文件写完了吗"，只能按 size+mtime 轮询判定稳定；
- 判定稳定后立即回调归档（.lrc → .ja.lrc，并删除 .lrc），使"翻译完成才算 A.lrc 存在"
  这条规则成立；
- 某个作品组内所有待转录音频都稳定后，回调一次 group_ready，供流水线立即派发翻译。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from io_adapter.file_scanner import find_rj_work_root


class LrcSettleWatcher:
    """轮询待转录音频对应的 <stem>.lrc，稳定即归档并通知作品组就绪。

    参数:
        audio_files: 本次待转录的音频文件列表
        settle_seconds: 文件 size+mtime 连续不变的最短持续时间（秒）
        poll_interval: 轮询间隔（秒）
        archive_cb: 归档回调，入参为 lrc 路径，返回是否成功归档
        group_ready_cb: 作品组全部稳定后的回调，入参为作品键（wkey）
        run_start_ts: 本次运行开始的时间戳；早于它的文件不归档，避免误伤历史产物
    """

    def __init__(
        self,
        audio_files: list[Path],
        *,
        settle_seconds: float = 2.0,
        poll_interval: float = 0.5,
        archive_cb: Callable[[Path], bool] | None = None,
        group_ready_cb: Callable[[str], None] | None = None,
        run_start_ts: float | None = None,
    ) -> None:
        self._audios = list(audio_files)
        self._settle = max(0.0, float(settle_seconds))
        self._poll = max(0.05, float(poll_interval))
        self._archive_cb = archive_cb
        self._group_ready_cb = group_ready_cb
        self._run_start_ts = run_start_ts if run_start_ts is not None else time.time()

        # 每个音频的采样状态：{audio: {'sig': (size, mtime_ns), 'since': ts}}
        self._state: dict[Path, dict] = {}
        self._settled: set[Path] = set()
        self._notified_groups: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # 作品组 → 组内音频列表
        self._groups: dict[str, list[Path]] = {}
        for audio in self._audios:
            self._groups.setdefault(self.group_key(audio), []).append(audio)

    # ---------- 分组 ----------

    @staticmethod
    def group_key(audio: Path) -> str:
        """音频所属作品键：RJ 根目录，找不到时用所在目录。"""
        rj_root, _ = find_rj_work_root(audio)
        return str(rj_root) if rj_root else str(audio.parent)

    def group_files(self) -> dict[str, list[Path]]:
        """作品键 → 该组待转录音频列表（供派发器枚举就绪组）。"""
        return {k: list(v) for k, v in self._groups.items()}

    # ---------- 采样 ----------

    def _sample_once(self) -> None:
        """检查一轮：更新稳定状态，归档已稳定的文件，通知已就绪的作品组。"""
        now = time.time()
        newly_settled: list[Path] = []

        for audio in self._audios:
            if audio in self._settled:
                continue
            lrc_path = audio.parent / f'{audio.stem}.lrc'
            try:
                st = lrc_path.stat()
            except OSError:
                # 文件还没出现：清掉采样状态，等下一轮
                self._state.pop(audio, None)
                continue

            sig = (st.st_size, st.st_mtime_ns)
            entry = self._state.get(audio)
            if entry is None or entry['sig'] != sig:
                self._state[audio] = {'sig': sig, 'since': now}
                continue

            if now - entry['since'] < self._settle:
                continue

            # 稳定达成：只归档本次运行新写的文件
            if st.st_mtime < self._run_start_ts - 1.0:
                self._settled.add(audio)
                continue
            if self._archive_cb is not None:
                try:
                    self._archive_cb(lrc_path)
                except Exception:
                    # 归档失败不阻塞流转，交给运行结束时的强制收尾重试
                    continue
            self._settled.add(audio)
            newly_settled.append(audio)

        if newly_settled:
            self._notify_ready_groups()

    def _notify_ready_groups(self) -> None:
        for group, audios in self._groups.items():
            if group in self._notified_groups:
                continue
            if all(a in self._settled for a in audios):
                self._notified_groups.add(group)
                if self._group_ready_cb is not None:
                    try:
                        self._group_ready_cb(group)
                    except Exception:
                        pass

    # ---------- 线程 ----------

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self._poll)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name='hdeg-transcribe-watch', daemon=True)
        self._thread.start()

    def finish(self, force: bool = True) -> None:
        """停止轮询；force=True 时对剩余音频做一次强制判定与归档。

        强制判定不等 settle 窗口，用于 infer.exe 已退出后的收尾。
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if not force:
            return

        for audio in self._audios:
            if audio in self._settled:
                continue
            lrc_path = audio.parent / f'{audio.stem}.lrc'
            if not lrc_path.exists():
                continue
            try:
                st = lrc_path.stat()
            except OSError:
                continue
            if st.st_mtime < self._run_start_ts - 1.0:
                self._settled.add(audio)
                continue
            if self._archive_cb is not None:
                try:
                    self._archive_cb(lrc_path)
                except Exception:
                    continue
            self._settled.add(audio)

        self._notify_ready_groups()

    def settled_count(self) -> int:
        with self._lock:
            return len(self._settled)

    def pending_groups(self) -> list[str]:
        """尚未通知就绪的作品键（收尾后仍可能为空）。"""
        return [g for g in self._groups if g not in self._notified_groups]

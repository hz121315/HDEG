# -*- coding: utf-8 -*-
"""transcribe_watch / 原子写 / 音轨键 的单元测试。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


# ── 转录看护 ──

def _mk_audio(tmp_path: Path, name: str = '01.mp3') -> Path:
    audio = tmp_path / name
    audio.write_bytes(b'fake-audio')
    return audio


def test_watcher_archives_settled_file(tmp_path):
    from pipeline.transcribe_watch import LrcSettleWatcher

    audio = _mk_audio(tmp_path)
    lrc = tmp_path / '01.lrc'
    lrc.write_text('[00:01.00]こんにちは\n', encoding='utf-8')

    archived = []
    ready = []
    watcher = LrcSettleWatcher(
        [audio], settle_seconds=0.1, poll_interval=0.02,
        archive_cb=lambda p: archived.append(p) or True,
        group_ready_cb=ready.append,
        run_start_ts=time.time() - 1,
    )
    watcher.start()
    time.sleep(0.5)
    watcher.finish(force=False)

    assert archived == [lrc]
    assert ready == [str(tmp_path)]


def test_watcher_skips_files_older_than_run(tmp_path):
    from pipeline.transcribe_watch import LrcSettleWatcher

    audio = _mk_audio(tmp_path)
    lrc = tmp_path / '01.lrc'
    lrc.write_text('[00:01.00]こんにちは\n', encoding='utf-8')
    old = time.time() - 3600
    os.utime(lrc, (old, old))

    archived = []
    watcher = LrcSettleWatcher(
        [audio], settle_seconds=0.05, poll_interval=0.02,
        archive_cb=lambda p: archived.append(p) or True,
        run_start_ts=time.time(),
    )
    watcher.finish(force=True)

    assert archived == []
    assert watcher.settled_count() == 1


def test_watcher_force_sweep_archives_without_waiting(tmp_path):
    from pipeline.transcribe_watch import LrcSettleWatcher

    audio = _mk_audio(tmp_path)
    (tmp_path / '01.lrc').write_text('[00:01.00]こんにちは\n', encoding='utf-8')

    archived = []
    watcher = LrcSettleWatcher(
        [audio], settle_seconds=10.0, poll_interval=10.0,
        archive_cb=lambda p: archived.append(p) or True,
        run_start_ts=time.time() - 1,
    )
    watcher.finish(force=True)

    assert len(archived) == 1


# ── 原子写 ──

def test_atomic_write_replaces_content(tmp_path):
    from io_adapter.lrc_handler import _atomic_write_lines

    target = tmp_path / 'a.lrc'
    target.write_text('old\n', encoding='utf-8')
    _atomic_write_lines(['new\n'], target)

    assert target.read_text(encoding='utf-8') == 'new\n'
    assert not (tmp_path / '.a.lrc.hdeg-tmp').exists()


def test_atomic_write_failure_keeps_old_file(tmp_path, monkeypatch):
    from io_adapter import lrc_handler

    target = tmp_path / 'a.lrc'
    target.write_text('old\n', encoding='utf-8')

    def _boom(src, dst):
        raise OSError('locked')

    monkeypatch.setattr(lrc_handler.os, 'replace', _boom)
    with pytest.raises(OSError):
        lrc_handler._atomic_write_lines(['new\n'], target)

    assert target.read_text(encoding='utf-8') == 'old\n'
    assert not (tmp_path / '.a.lrc.hdeg-tmp').exists()


# ── 音轨键 ──

def test_track_key_distinguishes_same_stem_in_subdirs(tmp_path):
    from pipeline.orchestrator import _track_key

    (tmp_path / '本編').mkdir()
    (tmp_path / '特典').mkdir()
    a = tmp_path / '本編' / '01.lrc'
    b = tmp_path / '特典' / '01.lrc'

    assert _track_key(tmp_path, a) == '本編/01'
    assert _track_key(tmp_path, b) == '特典/01'


def test_track_key_strips_ja_suffix(tmp_path):
    from pipeline.orchestrator import _track_key

    assert _track_key(tmp_path, tmp_path / '01.ja.lrc') == '01'
    assert _track_key(tmp_path, tmp_path / '01.lrc') == '01'


# ── 视频字幕生成 ──

def test_generate_srt_and_vtt_from_lrc(tmp_path):
    from io_adapter.lrc_handler import write_srt_from_lrc, write_vtt_from_lrc

    lrc = tmp_path / '01.lrc'
    lrc.write_text('[00:01.00]你好\n[00:03.50]再见\n', encoding='utf-8')

    assert write_srt_from_lrc(lrc, tmp_path / '01.srt')
    assert write_vtt_from_lrc(lrc, tmp_path / '01.vtt')

    srt = (tmp_path / '01.srt').read_text(encoding='utf-8')
    vtt = (tmp_path / '01.vtt').read_text(encoding='utf-8')
    assert '00:00:01,000 --> 00:00:03,500' in srt
    assert '你好' in srt
    assert vtt.startswith('WEBVTT')
    assert '00:00:01.000 --> 00:00:03.500' in vtt

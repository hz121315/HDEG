# -*- coding: utf-8 -*-
"""流式路径重试/统计/日志接线的离线回归测试。

覆盖本轮修复：
- 重复扫描去重后的 skipped 文件数口径（含汉化过滤与 target→audio 绑定）
- 失败入队保留 source_path/target_path（重试不再误读目标文件）
- _translate_task_stream 写入 work_track_runs 并转发进度回调
- translate_one_lrc 块级进度回调
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.orchestrator import (  # noqa: E402
    _collect_translation_pairs,
    _record_track_run,
    _translate_task_stream,
    translate_one_lrc,
)


class FakeEngine:
    """最小假引擎：默认成功，可脚本化返回拒答。"""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = 0

    def translate_batch(self, lines, **_kwargs):
        self.calls += 1
        action = self.script.pop(0) if self.script else {}
        base = {
            'finish_reason': 'stop', 'reasoning_preview': '',
            'hit_tokens': 0, 'miss_tokens': 0, 'prompt_tokens': 0,
            'completion_tokens': 0, 'reasoning_tokens': 0,
            'cost': 0.0, 'elapsed': 0.01, 'request_diagnostics': [],
        }
        if action.get('refuse'):
            base.update({
                'translated_lines': [''] * len(lines),
                'parsed_count': 0,
                'raw_response': '抱歉，我无法协助处理该内容。',
            })
            return base
        base.update({
            'translated_lines': [f'译文{i + 1}' for i in range(len(lines))],
            'parsed_count': len(lines),
            'raw_response': '{"translations": [...]}',
        })
        return base


class FakeTracker:
    def __init__(self):
        self.records = []

    def record(self, usage):
        self.records.append(usage)


def make_ctx(engine=None, *, nsfw_fallback_enabled=True):
    app = {
        'polish_after_translate': False,
        'export_ja_lrc': True,
        'lrc_max_lines_per_request': 0,
        'srt_vtt_resync': False,
        'save_track_logs': False,
    }
    return SimpleNamespace(
        config={'app': app, 'api': {}, 'transcription': {'audio_suffixes': 'mp3,wav'}},
        thread_lock=threading.Lock(),
        stats={
            'translated': 0, 'skipped': 0, 'success_lines': 0, 'total_lines': 0,
            'api_calls': 0, 'total_cost': 0.0, 'total_hit_tokens': 0,
            'total_miss_tokens': 0, 'total_completion_tokens': 0, 'archived': 0,
        },
        pr=SimpleNamespace(debug_enabled=False, token_inline=lambda u: None),
        tracker=FakeTracker(),
        pricing={'hit_per_1m': 0.02, 'miss_per_1m': 1, 'completion_per_1m': 2},
        translate_engine=engine or FakeEngine(),
        max_failed_retries=3,
        nsfw_fallback_enabled=nsfw_fallback_enabled,
        _desensitize_engine=None,
        _desensitize_locks={},
        _desensitize_locks_guard=threading.Lock(),
        retry_queue=[],
        retry_results={},
        abnormal_count=0,
        parallel_mode=False,
        work_track_runs={},
        work_expected_counts={},
        _work_log_announced=set(),
        _skip_counted_keys=set(),
    )


def write_lrc(path: Path, lines=('テスト行1', 'テスト行2')) -> Path:
    body = '\n'.join(f'[00:0{i + 1}.00]{t}' for i, t in enumerate(lines))
    path.write_text(body + '\n', encoding='utf-8')
    return path


def make_collect_ctx(skip_hanhua=False):
    """构造 _collect_translation_pairs 所需的最小 ctx。"""
    return SimpleNamespace(
        config={
            'app': {'skip_hanhua_files': skip_hanhua},
            'transcription': {'audio_suffixes': 'mp3,wav'},
        },
        thread_lock=threading.Lock(),
        stats={'skipped': 0},
        _skip_counted_keys=set(),
    )


def test_skipped_count_is_stable_across_repeated_scans(tmp_path):
    """重复扫描/汉化过滤/已存在译文只计一次，且不会把音频当 target。"""
    work = tmp_path / 'RJ01000001'
    work.mkdir()

    # 已有 A.lrc：按规则跳过
    (work / 'done.mp3').write_bytes(b'')
    write_lrc(work / 'done.lrc')
    # 同时存在 JA 与 A.lrc：只计一次跳过
    (work / 'both.mp3').write_bytes(b'')
    write_lrc(work / 'both.ja.lrc')
    write_lrc(work / 'both.lrc')
    # 待翻译：有 JA 无 A.lrc
    (work / 'todo.mp3').write_bytes(b'')
    write_lrc(work / 'todo.ja.lrc')
    # 待转录：只有音频
    (work / 'only_audio.mp3').write_bytes(b'')
    # 汉化过滤：同一音轨同时有音频与 JA 留档，只能计一次
    (work / 'track汉化.mp3').write_bytes(b'')
    write_lrc(work / 'track汉化.ja.lrc')

    ctx = make_collect_ctx(skip_hanhua=True)

    targets, sources, _audios = _collect_translation_pairs(work, ctx)
    first_skipped = ctx.stats['skipped']
    targets2, _, _ = _collect_translation_pairs(work, ctx)

    assert [p.name for p in targets] == ['todo.lrc']
    assert sources[targets[0]].name == 'todo.ja.lrc'
    assert [p.name for p in targets2] == ['todo.lrc']
    # done / both / track汉化 = 3；音频与 JA 属于同一轨，不得计两次
    assert first_skipped == 3
    assert ctx.stats['skipped'] == 3


def test_hanhua_switch_off_still_translates_hanhua_track(tmp_path):
    """关闭 skip_hanhua_files 时，汉化音频+JA 必须正常进入翻译。"""
    work = tmp_path / 'RJ01000005'
    work.mkdir()
    (work / 'track汉化.mp3').write_bytes(b'')
    write_lrc(work / 'track汉化.ja.lrc')

    ctx = make_collect_ctx(skip_hanhua=False)
    targets, sources, _ = _collect_translation_pairs(work, ctx)

    assert [p.name for p in targets] == ['track汉化.lrc']
    assert sources[targets[0]].name == 'track汉化.ja.lrc'
    assert ctx.stats['skipped'] == 0


def test_translated_target_not_counted_as_skipped_by_later_scan(tmp_path):
    """本轮已认领并写出的目标，补扫不得再计为“已有译文跳过”。"""
    work = tmp_path / 'RJ01000006'
    work.mkdir()
    write_lrc(work / 'track.ja.lrc')

    ctx = make_collect_ctx()
    targets, _, _ = _collect_translation_pairs(work, ctx)
    assert [p.name for p in targets] == ['track.lrc']
    assert ctx.stats['skipped'] == 0

    # 模拟本轮翻译成功写出目标后再次扫描
    write_lrc(work / 'track.lrc')
    targets2, _, _ = _collect_translation_pairs(work, ctx)
    assert targets2 == []
    assert ctx.stats['skipped'] == 0


def test_nested_same_name_tracks_kept_separate_in_work_log(tmp_path, monkeypatch):
    """同一作品不同子目录的同名音轨不能合并，作品日志应正常落盘。"""
    from pipeline import orchestrator

    work = tmp_path / 'RJ01000007'
    (work / 'main').mkdir(parents=True)
    (work / 'bonus').mkdir()
    main = work / 'main' / '01.lrc'
    bonus = work / 'bonus' / '01.lrc'
    write_lrc(main)
    write_lrc(bonus)

    ctx = make_ctx()
    ctx.work_expected_counts[str(work)] = 2
    log_calls = []
    monkeypatch.setattr(orchestrator, '_write_work_log',
                        lambda _ctx, wkey: log_calls.append(wkey))

    _record_track_run(ctx, str(work),
                      {'lrc_path': main, 'wkey': str(work), 'label': 'x', 'attempts': 0},
                      [], '成功', 1.0)
    assert log_calls == []  # 只完成 1/2

    _record_track_run(ctx, str(work),
                      {'lrc_path': bonus, 'wkey': str(work), 'label': 'x', 'attempts': 0},
                      [], '成功', 1.0)
    assert set(ctx.work_track_runs[str(work)]) == {'main/01.lrc', 'bonus/01.lrc'}
    assert log_calls == [str(work)]


def test_stream_status_ignores_other_work_same_name_failure(tmp_path, monkeypatch):
    """另一作品同名轨的失败标记不能污染本作品的跳过状态。"""
    from pipeline import orchestrator

    work = tmp_path / 'RJ01000008'
    work.mkdir()
    target = work / '01.lrc'
    write_lrc(target)

    ctx = make_ctx()
    ctx.retry_results['01.lrc'] = '失败: other work refusal'
    monkeypatch.setattr(orchestrator, 'translate_one_lrc', lambda *a, **k: False)

    task = {'lrc_path': target, 'wkey': str(work), 'label': 'x', 'attempts': 0}
    assert _translate_task_stream(ctx, task) is False
    assert ctx.retry_queue == []
    assert task['_last_status'] == '跳过'
    assert ctx.work_track_runs[str(work)]['01.lrc']['status'] == '跳过'


def _raise_permission(*_args, **_kwargs):
    raise PermissionError('mock')


def test_stream_final_exception_marks_final_failed(tmp_path, monkeypatch):
    """末轮异常不再入队，必须留下最终失败状态供重试循环识别。"""
    from pipeline import orchestrator

    work = tmp_path / 'RJ01000009'
    work.mkdir()
    target = work / '01.lrc'
    write_lrc(target)

    ctx = make_ctx()
    ctx.max_failed_retries = 1
    monkeypatch.setattr(orchestrator, 'translate_one_lrc', _raise_permission)

    task = {'lrc_path': target, 'wkey': str(work), 'label': 'x', 'attempts': 1}
    assert _translate_task_stream(ctx, task) is False
    assert ctx.retry_queue == []
    assert task['_last_status'] == '最终失败'
    assert ctx.stats['skipped'] == 1


def test_progress_completion_after_successful_write(tmp_path, monkeypatch):
    """“完成”进度必须发生在字幕成功写入之后。"""
    from io_adapter import lrc_handler

    work = tmp_path / 'RJ01000010'
    work.mkdir()
    src = write_lrc(work / 'track.ja.lrc')
    target = work / 'track.lrc'

    events = []
    real_write = lrc_handler.write_subtitle_file_nonempty

    def _write(*args, **kwargs):
        events.append('WRITE')
        return real_write(*args, **kwargs)

    monkeypatch.setattr(lrc_handler, 'write_subtitle_file_nonempty', _write)
    ctx = make_ctx(FakeEngine())
    ok = translate_one_lrc(
        target, ctx, source_path=src, target_path=target,
        progress_callback=lambda line: events.append(('PROGRESS', line)),
    )

    assert ok is True
    write_idx = events.index('WRITE')
    done_idx = next(i for i, e in enumerate(events)
                    if isinstance(e, tuple) and '完成' in e[1])
    assert write_idx < done_idx


def test_progress_does_not_claim_completion_on_write_failure(tmp_path, monkeypatch):
    """写盘失败时不能已经显示“完成”。"""
    from io_adapter import lrc_handler

    work = tmp_path / 'RJ01000011'
    work.mkdir()
    src = write_lrc(work / 'track.ja.lrc')
    target = work / 'track.lrc'
    progress = []

    monkeypatch.setattr(lrc_handler, 'write_subtitle_file_nonempty', _raise_permission)
    ctx = make_ctx(FakeEngine())
    with pytest.raises(PermissionError):
        translate_one_lrc(
            target, ctx, source_path=src, target_path=target,
            progress_callback=progress.append,
        )

    assert any('译文已返回' in line for line in progress)
    assert not any('完成' in line for line in progress)


def test_progress_window_keeps_stdout_and_escapes_label(tmp_path, monkeypatch):
    """进度窗不能吞掉 PowerShell 的 stdout，目录名中的单引号必须转义。"""
    from pipeline import work_windows

    captured = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            captured['args'] = args
            captured['kwargs'] = kwargs

        def poll(self):
            return None

    monkeypatch.setattr(work_windows, '_IS_WINDOWS', True)
    monkeypatch.setattr(work_windows.subprocess, 'Popen', FakePopen)

    win = work_windows.WorkProgressWindow('key', "O'Brien", tmp_path)
    win.open()

    assert 'stdout' not in captured['kwargs']
    script = captured['args'][0][-1]
    assert "O''Brien" in script


def test_retry_enqueue_preserves_source_and_target_paths(tmp_path):
    """翻译失败入队时必须带上源/目标路径，重试不能把目标当源。"""
    work = tmp_path / 'RJ01000002'
    work.mkdir()
    src = write_lrc(work / 'track1.ja.lrc')
    target = work / 'track1.lrc'

    ctx = make_ctx(FakeEngine([{'refuse': True}]), nsfw_fallback_enabled=False)
    ok = translate_one_lrc(
        target, ctx,
        source_path=src, target_path=target,
        attempts=0, label='测试作品', wkey=str(work),
    )

    assert ok is False
    assert len(ctx.retry_queue) == 1
    entry = ctx.retry_queue[0]
    assert entry['lrc_path'] == target
    assert entry['source_path'] == src
    assert entry['target_path'] == target


def test_translate_task_stream_records_run_and_forwards_progress(tmp_path, monkeypatch):
    """流式单轨任务成功时写 work_track_runs，并把进度回调透传给翻译函数。"""
    from pipeline import orchestrator

    work = tmp_path / 'RJ01000003'
    work.mkdir()
    target = work / 'track1.lrc'
    target.write_text('[00:01.00]原文\n', encoding='utf-8')
    seen_progress = []

    def fake_translate(_lrc_path, _ctx, **kwargs):
        callback = kwargs.get('progress_callback')
        if callback:
            callback('进度-1')
        return True

    monkeypatch.setattr(orchestrator, 'translate_one_lrc', fake_translate)

    ctx = make_ctx()
    ctx.work_expected_counts[str(work)] = 1
    task = {
        'lrc_path': target,
        'source_path': work / 'track1.ja.lrc',
        'label': '测试作品',
        'wkey': str(work),
        'progress_callback': seen_progress.append,
        'attempts': 0,
    }

    assert _translate_task_stream(ctx, task) is True
    entry = ctx.work_track_runs[str(work)]['track1.lrc']
    assert entry['status'] == '成功'
    assert seen_progress == ['进度-1']


def test_translate_one_lrc_emits_chunk_progress(tmp_path):
    """translate_one_lrc 在解析/成功节点触发进度回调。"""
    work = tmp_path / 'RJ01000004'
    work.mkdir()
    src = write_lrc(work / 'track1.ja.lrc')
    target = work / 'track1.lrc'

    ctx = make_ctx(FakeEngine())
    lines = []
    ok = translate_one_lrc(
        target, ctx,
        source_path=src, target_path=target,
        progress_callback=lines.append,
    )

    assert ok is True
    assert any('解析' in line for line in lines)
    assert any('完成' in line for line in lines)

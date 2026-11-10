# -*- coding: utf-8 -*-
"""NSFW 多级降级重试离线测试（mock 翻译引擎，不触发真实 API）。

覆盖：
- 第1级成功（旧行为不变）
- 第1级拒答 → 复用/生成和谐版世界观 → 第2级成功
- 4 级全部失败 → 跳过音轨（不入轮级重试队列）+ 失败日志 4 条
- 开关关闭 → 失败仅 1 次调用，走旧轮级重试队列
- 无世界观 → 直接从第1级降到第4级裸翻
- 翻译全空（parsed_count>0 但全空串）同样触发降级
- harmonize_worldview_with_llm 单元（JSON 改写 / _source 剥离 / 拒答返回 None）
- 和谐版文件生成后同组复用（不重复调用 LLM）
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.orchestrator import translate_one_lrc  # noqa: E402


# ==================== 测试基建 ====================

class FakeEngine:
    """按脚本回放的假翻译引擎。

    script 为每次 translate_batch 的行为列表，元素：
        {'raise': '异常文本'}                     → 抛异常
        {'refuse': True, 'raw': '...'}            → 拒答（parsed_count=0）
        {'empty': True}                           → 全空串（parsed_count>0 但无内容）
        {}                                        → 正常成功
    """

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[dict] = []

    def translate_batch(self, lines, *, terms=None, alias_list=None,
                        worldview=None, **kwargs):
        self.calls.append({
            'terms': terms, 'alias_list': alias_list,
            'worldview': worldview, 'n_lines': len(lines),
        })
        act = self.script.pop(0) if self.script else {}
        base = {
            'finish_reason': 'stop', 'reasoning_preview': '',
            'hit_tokens': 10, 'miss_tokens': 20, 'prompt_tokens': 30,
            'completion_tokens': 40, 'cost': 0.001, 'prompt_tokens': 30,
            'reasoning_tokens': 0, 'elapsed': 0.1,
        }
        if act.get('raise'):
            raise RuntimeError(act['raise'])
        if act.get('refuse'):
            base.update({
                'translated_lines': [''] * len(lines),
                'parsed_count': 0,
                'raw_response': act.get('raw', '抱歉，我无法协助处理该内容。'),
            })
            return base
        if act.get('empty'):
            base.update({
                'translated_lines': [''] * len(lines),
                'parsed_count': len(lines),
                'raw_response': '{"translations": []}',
            })
            return base
        base.update({
            'translated_lines': [f'中文译文{i+1}' for i in range(len(lines))],
            'parsed_count': len(lines),
            'raw_response': '{"translations": [...]}',
        })
        return base


class FakeTracker:
    def __init__(self):
        self.records = []

    def record(self, usage):
        self.records.append(usage)

    def compute_cost(self, hit, miss, comp):
        return 0.0


def make_ctx(engine, *, nsfw_fallback_enabled=True, config_app=None):
    """构造满足 translate_one_lrc 所需的最小 ctx（鸭子类型）。"""
    app = {
        'polish_after_translate': False,
        'export_ja_lrc': True,
        'lrc_max_lines_per_request': 0,   # 整文件翻译（单块）
        'srt_vtt_resync': False,
    }
    app.update(config_app or {})
    return SimpleNamespace(
        config={'app': app, 'api': {}},
        thread_lock=threading.Lock(),
        stats={
            'translated': 0, 'skipped': 0, 'success_lines': 0, 'total_lines': 0,
            'api_calls': 0, 'total_cost': 0.0, 'total_hit_tokens': 0,
            'total_miss_tokens': 0, 'total_completion_tokens': 0, 'archived': 0,
        },
        pr=SimpleNamespace(debug_enabled=False, token_inline=lambda u: None),
        tracker=FakeTracker(),
        translate_engine=engine,
        max_failed_retries=3,
        nsfw_fallback_enabled=nsfw_fallback_enabled,
        _harmonize_engine=None,
        _harmonize_locks={},
        _harmonize_locks_guard=threading.Lock(),
        retry_queue=[],
        retry_results={},
        abnormal_count=0,
        parallel_mode=False,
    )


def write_ja_lrc(work_dir: Path, name: str = 'track1') -> Path:
    """写一个 6 行日文 .ja.lrc，返回源路径（目标为同名 .lrc）。"""
    lines = [f"[00:0{i}.00]テスト行{i}" for i in range(1, 7)]
    src = work_dir / f'{name}.ja.lrc'
    src.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return src


ORIG_WV = {
    'worldview': '父亲与17岁女儿的禁忌故事。',
    'characters': [
        {'name': 'お父さん', 'name_cn': '爸爸', 'role': '父亲', 'personality': '温柔',
         'relationship': '父女'},
    ],
    'themes': ['近亲'],
    'special_terms': {'テスト': '测试'},
}

HARMONIZED_WV = {
    'worldview': '关系亲密的两人之间的故事。',
    'characters': [
        {'name': 'お父さん', 'name_cn': '伴侣', 'role': '同居人', 'personality': '温柔',
         'relationship': '关系亲密'},
    ],
    'themes': ['恋爱'],
    'special_terms': {'テスト': '测试'},
}


def run_translate(ctx, work_dir: Path, name: str = 'track1',
                  *, terms=None, alias_list=None, worldview=None):
    src = write_ja_lrc(work_dir, name)
    target = work_dir / f'{name}.lrc'
    ok = translate_one_lrc(
        target, ctx,
        terms=terms, alias_list=alias_list, worldview=worldview,
        scriptbook_lines=None, attempts=0, label='测试作品',
        wkey=str(work_dir), source_path=src, target_path=target,
    )
    return ok, target


# ==================== 用例 ====================

def test_level0_success(tmp_path):
    """第1级直接成功：行为与旧路径一致，只调用一次。"""
    work = tmp_path / 'RJ01000001'
    work.mkdir()
    engine = FakeEngine([{}])
    ctx = make_ctx(engine)
    ok, target = run_translate(ctx, work, worldview=dict(ORIG_WV))
    assert ok is True
    assert len(engine.calls) == 1
    assert engine.calls[0]['worldview'] == ORIG_WV
    assert target.exists()
    assert ctx.stats['translated'] == 1
    assert ctx.stats['skipped'] == 0


def test_refuse_then_harmonized_file_success(tmp_path):
    """第1级拒答 → 读取已有和谐版文件 → 第2级成功，日志记录第1次失败。"""
    work = tmp_path / 'RJ01000002'
    work.mkdir()
    # 预置同组其他音轨生成的和谐版文件
    (work / '和谐版.worldview.json').write_text(
        json.dumps(HARMONIZED_WV, ensure_ascii=False), encoding='utf-8')

    engine = FakeEngine([{'refuse': True}, {}])
    ctx = make_ctx(engine)
    ok, target = run_translate(ctx, work, worldview=dict(ORIG_WV))
    assert ok is True
    assert len(engine.calls) == 2
    # 第2次请求带的是和谐版世界观
    assert engine.calls[1]['worldview'] == HARMONIZED_WV
    assert target.exists()
    assert ctx.stats['skipped'] == 0
    assert ctx.retry_queue == []
    # 失败日志：仅第1次失败被记录，含原始返回
    log_file = work / 'log' / 'track1.log'
    assert log_file.exists()
    content = log_file.read_text(encoding='utf-8')
    assert '第1次请求' in content
    assert '抱歉，我无法协助处理该内容。' in content
    assert '第2次请求' not in content


def test_all_levels_fail_skip(tmp_path):
    """4 级全部失败 → 跳过音轨，不入轮级重试队列，日志含 4 条记录。"""
    work = tmp_path / 'RJ01000003'
    work.mkdir()
    (work / '和谐版.worldview.json').write_text(
        json.dumps(HARMONIZED_WV, ensure_ascii=False), encoding='utf-8')

    engine = FakeEngine([{'refuse': True}] * 4)
    ctx = make_ctx(engine)
    ok, target = run_translate(ctx, work, worldview=dict(ORIG_WV),
                               terms={'テスト': '测试'}, alias_list=['エイリアス'])
    assert ok is False
    assert len(engine.calls) == 4
    # 级别上下文断言
    assert engine.calls[0]['worldview'] == ORIG_WV          # L1 完整
    assert engine.calls[1]['worldview'] == HARMONIZED_WV    # L2 和谐版世界观
    assert engine.calls[2]['worldview'] == {'characters': HARMONIZED_WV['characters']}  # L3 无世界观
    assert engine.calls[3]['worldview'] is None             # L4 裸翻
    assert engine.calls[3]['terms'] is None
    assert engine.calls[3]['alias_list'] is None
    # L2/L3 术语保留
    assert engine.calls[1]['terms'] == {'テスト': '测试'}
    assert engine.calls[2]['terms'] == {'テスト': '测试'}
    assert not target.exists()
    assert ctx.stats['skipped'] == 1
    assert ctx.retry_queue == []
    # retry_results 不以"失败"开头：批式/串行外层据此判"跳过"而非"待重试"
    assert ctx.retry_results['track1.lrc'].startswith('多级降级后跳过')
    log_file = work / 'log' / 'track1.log'
    content = log_file.read_text(encoding='utf-8')
    for lv in ('第1次请求', '第2次请求', '第3次请求', '第4次请求'):
        assert lv in content
    # 不同请求间有分隔
    assert content.count('══') >= 4


def test_fallback_disabled_keeps_old_behavior(tmp_path):
    """开关关闭：失败仅 1 次调用，进入轮级重试队列，不产生失败日志。"""
    work = tmp_path / 'RJ01000004'
    work.mkdir()
    engine = FakeEngine([{'refuse': True}])
    ctx = make_ctx(engine, nsfw_fallback_enabled=False)
    ok, target = run_translate(ctx, work, worldview=dict(ORIG_WV))
    assert ok is False
    assert len(engine.calls) == 1
    assert not target.exists()
    assert len(ctx.retry_queue) == 1
    assert ctx.retry_queue[0]['lrc_path'] == target
    assert not (work / 'log').exists()


def test_no_worldview_drops_to_level4(tmp_path):
    """无世界观可和谐：第2/3级跳过，第2次调用即第4级裸翻。"""
    work = tmp_path / 'RJ01000005'
    work.mkdir()
    engine = FakeEngine([{'refuse': True}, {}])
    ctx = make_ctx(engine)
    ok, target = run_translate(ctx, work, worldview=None)
    assert ok is True
    assert len(engine.calls) == 2
    assert engine.calls[1]['worldview'] is None
    assert engine.calls[1]['terms'] is None
    assert target.exists()


def test_empty_translation_triggers_fallback(tmp_path):
    """翻译全空（parsed_count>0 但全部空串）同样触发降级。"""
    work = tmp_path / 'RJ01000006'
    work.mkdir()
    (work / '和谐版.worldview.json').write_text(
        json.dumps(HARMONIZED_WV, ensure_ascii=False), encoding='utf-8')
    engine = FakeEngine([{'empty': True}, {}])
    ctx = make_ctx(engine)
    ok, target = run_translate(ctx, work, worldview=dict(ORIG_WV))
    assert ok is True
    assert len(engine.calls) == 2
    log_file = work / 'log' / 'track1.log'
    content = log_file.read_text(encoding='utf-8')
    assert '翻译全空' in content


def test_api_exception_triggers_fallback(tmp_path):
    """API 异常（报错拒绝）同样触发降级并记录异常文本。"""
    work = tmp_path / 'RJ01000007'
    work.mkdir()
    (work / '和谐版.worldview.json').write_text(
        json.dumps(HARMONIZED_WV, ensure_ascii=False), encoding='utf-8')
    engine = FakeEngine([{'raise': 'API 调用失败：content policy violation'}, {}])
    ctx = make_ctx(engine)
    ok, target = run_translate(ctx, work, worldview=dict(ORIG_WV))
    assert ok is True
    assert len(engine.calls) == 2
    content = (work / 'log' / 'track1.log').read_text(encoding='utf-8')
    assert 'content policy violation' in content


# ==================== 和谐化 LLM 单元 ====================

class FakeCallApiEngine:
    def __init__(self, reply, stats=None):
        self.reply = reply
        self.stats = stats or {'prompt_tokens': 5, 'hit_tokens': 1, 'miss_tokens': 4,
                               'completion_tokens': 2, 'reasoning_tokens': 0}
        self.prompts: list[tuple[str, str]] = []

    def call_api(self, system_prompt, user_prompt, **kwargs):
        self.prompts.append((system_prompt, user_prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply, self.stats


def test_harmonize_worldview_with_llm_ok():
    from engines.worldview_engine import harmonize_worldview_with_llm
    wv = dict(ORIG_WV)
    wv['_source'] = ['内部元数据.json']
    reply = json.dumps(HARMONIZED_WV, ensure_ascii=False)
    eng = FakeCallApiEngine(reply)
    result, stats = harmonize_worldview_with_llm(eng, wv)
    assert result == HARMONIZED_WV
    assert stats['prompt_tokens'] == 5
    # _source 不发送给 LLM
    sys_p, user_p = eng.prompts[0]
    assert '_source' not in user_p
    assert '内部元数据' not in user_p
    assert '合规' in sys_p


def test_harmonize_worldview_with_llm_refuse():
    from engines.worldview_engine import harmonize_worldview_with_llm
    eng = FakeCallApiEngine('我很抱歉，但我无法处理这个请求。')
    result, stats = harmonize_worldview_with_llm(eng, dict(ORIG_WV))
    assert result is None

    eng2 = FakeCallApiEngine(RuntimeError('API 调用失败：403'))
    result2, _ = harmonize_worldview_with_llm(eng2, dict(ORIG_WV))
    assert result2 is None


def test_harmonize_extra_system_prompt_prepended():
    """自定义前置 prompt 拼接在内置规则之前，内置格式约束仍在。"""
    from engines.worldview_engine import (
        HARMONIZE_SYSTEM_PROMPT, harmonize_worldview_with_llm,
    )
    extra = '血腥、肢解、酷刑等描写一律改写为"激烈冲突"等温和表述。'
    eng = FakeCallApiEngine(json.dumps(HARMONIZED_WV, ensure_ascii=False))
    result, _ = harmonize_worldview_with_llm(eng, dict(ORIG_WV),
                                             extra_system_prompt=extra)
    assert result == HARMONIZED_WV
    sys_p, _ = eng.prompts[0]
    assert sys_p.startswith(extra)                  # 前置在前
    assert HARMONIZE_SYSTEM_PROMPT in sys_p         # 内置规则兜底在后
    # 空白自定义 → 纯内置
    eng2 = FakeCallApiEngine(json.dumps(HARMONIZED_WV, ensure_ascii=False))
    harmonize_worldview_with_llm(eng2, dict(ORIG_WV), extra_system_prompt='   ')
    assert eng2.prompts[0][0] == HARMONIZE_SYSTEM_PROMPT


def test_load_harmonize_prompt(tmp_path):
    """_load_harmonize_prompt：文件存在读内容，缺失/为空/未配置回退 None。"""
    from pipeline import orchestrator as orch

    ctx = make_ctx(FakeEngine(None))
    # 未配置
    assert orch._load_harmonize_prompt(ctx) is None

    # 文件存在
    pf = tmp_path / 'harmonize_prefix.txt'
    pf.write_text('补充规则：血腥描写改写为激烈冲突。', encoding='utf-8')
    ctx.config = {'app': ctx.config['app'], 'api': {},
                  'prompts': {'harmonize_system_prompt_file': str(pf)}}
    assert orch._load_harmonize_prompt(ctx) == '补充规则：血腥描写改写为激烈冲突。'

    # 文件不存在
    ctx.config['prompts']['harmonize_system_prompt_file'] = str(tmp_path / 'nope.txt')
    assert orch._load_harmonize_prompt(ctx) is None

    # 空文件
    pf2 = tmp_path / 'empty.txt'
    pf2.write_text('   ', encoding='utf-8')
    ctx.config['prompts']['harmonize_system_prompt_file'] = str(pf2)
    assert orch._load_harmonize_prompt(ctx) is None


def test_ensure_harmonized_generates_and_reuses(tmp_path, monkeypatch):
    """和谐版不存在时通过 LLM 生成并落盘；同组第二次失败直接复用文件。"""
    import engines.worldview_engine as wve
    from pipeline import orchestrator as orch

    work = tmp_path / 'RJ01000008'
    work.mkdir()
    gen_calls = []

    def fake_harmonize(engine, worldview, verbose=False, extra_system_prompt=None):
        gen_calls.append(1)
        return dict(HARMONIZED_WV), {'prompt_tokens': 1, 'hit_tokens': 1,
                                     'miss_tokens': 0, 'completion_tokens': 1}

    monkeypatch.setattr(wve, 'harmonize_worldview_with_llm', fake_harmonize)

    ctx = make_ctx(FakeEngine(None))
    ctx._harmonize_engine = 'fake-engine'   # 跳过真实引擎构造

    out1 = orch._ensure_harmonized_worldview(ctx, str(work), dict(ORIG_WV), '测试')
    assert out1 == HARMONIZED_WV
    assert len(gen_calls) == 1
    assert (work / '和谐版.worldview.json').exists()

    # 第二次（同组其他音轨）：不重新生成
    out2 = orch._ensure_harmonized_worldview(ctx, str(work), dict(ORIG_WV), '测试')
    assert out2 == HARMONIZED_WV
    assert len(gen_calls) == 1


def test_ensure_harmonized_no_worldview(tmp_path):
    """无原始世界观：直接返回 None，不生成文件。"""
    from pipeline import orchestrator as orch
    work = tmp_path / 'RJ01000009'
    work.mkdir()
    ctx = make_ctx(FakeEngine(None))
    assert orch._ensure_harmonized_worldview(ctx, str(work), None, '测试') is None
    assert not (work / '和谐版.worldview.json').exists()


def test_harmonize_file_paths(tmp_path):
    from engines.worldview_engine import (
        get_harmonized_worldview_path, load_harmonized_worldview,
        save_harmonized_worldview,
    )
    p = get_harmonized_worldview_path(tmp_path)
    assert p.name == '和谐版.worldview.json'
    assert load_harmonized_worldview(tmp_path) == {}
    save_harmonized_worldview(tmp_path, HARMONIZED_WV)
    assert load_harmonized_worldview(tmp_path) == HARMONIZED_WV

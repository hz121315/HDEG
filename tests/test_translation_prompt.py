# -*- coding: utf-8 -*-
"""翻译内置 system prompt 抽取为 prompts/translation.txt 的测试。

覆盖：
- 内置段从文件加载且内容完整（含台本/标点等板块）
- system_prompt_file 为空时使用该文件（术语等附加段不混入）
- 外部 system_prompt_file 完整替换基础段
- 相对路径在 CWD 找不到时按程序目录兜底解析（发布件场景）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from engines import translate_engine as te  # noqa: E402


def _make_engine(**kwargs) -> te.OpenAICompatEngine:
    return te.OpenAICompatEngine(
        {'model': 'mock', 'base_url': 'http://127.0.0.1:1', 'generation_params': {}},
        verbose=False, **kwargs)


def test_builtin_prompt_loaded_from_file():
    prompt = te.load_builtin_translation_prompt()
    assert prompt
    assert '【内容安全与忠实翻译' in prompt
    assert '【翻译忠实度与风格约束' in prompt
    # 台本/标点已写死内置，不再出现在可编辑文件中（避免重复）
    assert '台本（scriptbook）优先级' not in prompt
    assert '字幕标点符号规范' not in prompt
    # 附加段（术语/世界观）不属于内置文件内容
    assert '【术语对照表' not in prompt
    # 缓存生效
    assert te.load_builtin_translation_prompt() == prompt


def test_builtin_mechanical_rules_hardcoded():
    """台本优先级与标点规范写死内置常量，内容完整。"""
    assert '台本（scriptbook）优先级' in te.TRANSLATION_BUILTIN_SCRIPTBOOK_RULES
    assert '<scriptbook>' in te.TRANSLATION_BUILTIN_SCRIPTBOOK_RULES
    assert 'sb 缺失' in te.TRANSLATION_BUILTIN_SCRIPTBOOK_RULES
    assert '字幕标点符号规范' in te.TRANSLATION_BUILTIN_PUNCTUATION_RULES
    assert '禁用省略号' in te.TRANSLATION_BUILTIN_PUNCTUATION_RULES
    assert '句末禁句号' in te.TRANSLATION_BUILTIN_PUNCTUATION_RULES


def test_build_system_prompt_uses_builtin_file():
    eng = _make_engine()
    base = te.load_builtin_translation_prompt()
    sp = eng.build_system_prompt()
    assert sp.startswith(base)
    assert te.TRANSLATION_BUILTIN_SCRIPTBOOK_RULES in sp
    assert te.TRANSLATION_BUILTIN_PUNCTUATION_RULES in sp


def test_external_prompt_file_replaces_base_but_keeps_hardcoded_rules(tmp_path):
    """自定义文件替换基础段，但台本/标点通用规则始终内置附加。"""
    pf = tmp_path / 'custom.txt'
    pf.write_text('自定义基础段。', encoding='utf-8')
    eng = _make_engine(system_prompt_file=str(pf))
    sp = eng.build_system_prompt()
    assert sp.startswith('自定义基础段。')
    assert '【内容安全与忠实翻译' not in sp          # 内置基础段未残留
    assert te.TRANSLATION_BUILTIN_SCRIPTBOOK_RULES in sp
    assert te.TRANSLATION_BUILTIN_PUNCTUATION_RULES in sp


def test_builtin_prompt_missing_all_paths(monkeypatch):
    """全部候选路径都不存在：告警一次并返回空串，不抛异常。"""
    from pathlib import Path as _P

    monkeypatch.setattr(te, '_BUILTIN_PROMPT_CACHE', None)
    monkeypatch.setattr(te, '_builtin_prompt_candidates',
                        lambda: [_P('Z:/不存在/prompts/translation.txt')])
    assert te.load_builtin_translation_prompt() == ''
    # 该分支不抛异常即通过（历史上 _p 被循环变量遮蔽导致 TypeError）


def test_relative_system_prompt_falls_back_to_app_dir(tmp_path, monkeypatch):
    """相对路径在 CWD 找不到时，按程序目录解析（发布件 CWD 不保证一致）。"""
    import io_adapter.config_loader as cl

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cl, 'get_app_dir', lambda: _REPO_ROOT)
    eng = _make_engine(system_prompt_file='prompts/translation.txt')
    # 外部文件按原文读取（保留末尾换行），与内置加载仅差 strip
    assert eng.build_system_prompt().startswith(te.load_builtin_translation_prompt())

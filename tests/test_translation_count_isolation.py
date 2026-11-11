# -*- coding: utf-8 -*-
"""共享翻译引擎的解析计数隔离测试：只使用假 API，不访问网络。"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from engines.translate_engine import OpenAICompatEngine


def make_engine(auto_retry=False):
    return OpenAICompatEngine({
        'model': 'mock', 'protocol': 'openai',
        'generation_params': {'auto_retry_on_empty': auto_retry},
    }, verbose=False)


def stats():
    return {'hit_tokens': 0, 'miss_tokens': 0, 'completion_tokens': 0}


def response(count):
    return json.dumps({'zh': ['译文'] * count}, ensure_ascii=False)


@pytest.mark.parametrize('first_count,second_count', [(6, 0), (0, 6), (2, 6)])
def test_concurrent_calls_keep_their_own_count(monkeypatch, first_count, second_count):
    """在首请求解析完成后暂停，让第二请求完整返回，再恢复首请求。"""
    engine = make_engine()
    first_parsed = threading.Event()
    release_first = threading.Event()
    parse = engine._parse_json_translation_with_count

    def call_api(system_prompt, user_prompt):
        count = first_count if 'track-first' in user_prompt else second_count
        return (response(count) if count else 'not json'), stats()

    def parse_interleaved(lines, raw):
        result = parse(lines, raw)
        if 'track-first' in lines[0]:
            first_parsed.set()
            assert release_first.wait(5), '第二请求未在时限内完成'
        return result

    monkeypatch.setattr(engine, 'call_api', call_api)
    monkeypatch.setattr(engine, '_parse_json_translation_with_count', parse_interleaved)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(engine.translate_batch, ['track-first'] * 6,
                            skip_hallucination_check=True)
        try:
            assert first_parsed.wait(5), '首请求未完成解析'
            second = pool.submit(engine.translate_batch, ['track-second'] * 6,
                                 skip_hallucination_check=True).result(timeout=5)
        finally:
            release_first.set()
        first = first.result(timeout=5)

    assert first['parsed_count'] == first_count
    assert second['parsed_count'] == second_count
    assert sum(bool(t) for t in first['translated_lines']) == first_count
    assert sum(bool(t) for t in second['translated_lines']) == second_count


def test_public_parser_keeps_two_value_contract():
    engine = make_engine()
    original, translated = engine.parse_json_translation(['原文'], response(1))
    assert original == ['原文']
    assert translated == ['译文']
    assert not hasattr(engine, '_last_parsed_count')


@pytest.mark.parametrize('initial,retry,expected_count', [
    ('not json', response(6), 6),
    (response(6).replace('译文', ''), 'not json', 6),
])
def test_auto_retry_count_matches_selected_response(monkeypatch, initial, retry, expected_count):
    """采用重试结果则同步计数；放弃重试结果则保留原响应及其计数。"""
    engine = make_engine(auto_retry=True)
    replies = iter([initial, retry])
    monkeypatch.setattr(engine, 'call_api', lambda *a, **k: (next(replies), stats()))
    result = engine.translate_batch(['原文'] * 6, skip_hallucination_check=True)
    assert result['parsed_count'] == expected_count
    assert result['raw_response'] == (retry if initial == 'not json' else initial)


def test_salvaged_count_is_returned_with_translation(monkeypatch):
    engine = make_engine()
    monkeypatch.setattr(engine, '_extract_json_array', lambda raw: None)
    monkeypatch.setattr(OpenAICompatEngine, '_salvage_indexed_items',
                        staticmethod(lambda raw, lines: {1: '甲', 3: '丙', 5: '戊'}))
    original, translated, count = engine._parse_json_translation_with_count(['原文'] * 6, 'partial')
    assert count == 3
    assert translated == ['甲', '', '丙', '', '戊', '']

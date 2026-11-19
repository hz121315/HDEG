# -*- coding: utf-8 -*-
"""脱敏命名迁移兼容：仅临时配置、中性假引擎和本地日志。"""
from __future__ import annotations

import copy
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from io_adapter.config_loader import load_config
from pipeline import orchestrator as orch
from pipeline import translation_prepare as prep
from utils.token_tracker import TokenTracker, TokenUsage


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('脱敏配置测试禁止网络连接')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    for name in ('HDEG_API_KEY', 'HDEG_MODEL', 'HDEG_BASE_URL', 'HDEG_PROTOCOL', 'HDEG_CONFIG'):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake_engines(monkeypatch):
    from engines import translate_engine

    created = []

    def create(config, **kwargs):
        engine = SimpleNamespace(config=config)
        created.append(engine)
        return engine

    monkeypatch.setattr(translate_engine, 'create_translate_engine', create)
    return created


@pytest.mark.parametrize('fields,expected', [
    ({}, {'model': 'legacy', 'generation_params': {'stop': ['end']}}),
    ({'desensitize': {'model': 'current'}}, {'model': 'current'}),
    ({'desensitize': {}}, {}),
])
def test_load_config_api_presence_priority_and_deep_copy(tmp_path, fields, expected):
    legacy = {'model': 'legacy', 'generation_params': {'stop': ['end']}}
    config = {'api': {'harmonize': legacy, **copy.deepcopy(fields)}}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    original_bytes = path.read_bytes()

    loaded = load_config(path)
    assert loaded['api']['desensitize'] == expected
    assert loaded['api']['harmonize'] == legacy
    if 'desensitize' not in fields:
        # 不仅复制外层：旧端点的嵌套生成参数也不能与新字段共享引用。
        assert loaded['api']['desensitize'] is not loaded['api']['harmonize']
        loaded['api']['desensitize']['generation_params']['stop'].append('other')
        assert loaded['api']['harmonize'] == legacy
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize('fields,expected', [
    ({}, 'legacy.txt'),
    ({'desensitize_system_prompt_file': 'current.txt'}, 'current.txt'),
    ({'desensitize_system_prompt_file': ''}, ''),
])
def test_load_config_prompt_presence_priority_without_writeback(tmp_path, fields, expected):
    config = {'prompts': {'harmonize_system_prompt_file': 'legacy.txt', **fields}}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    original_bytes = path.read_bytes()

    loaded = load_config(path)
    assert loaded['prompts']['desensitize_system_prompt_file'] == expected
    assert loaded['prompts']['harmonize_system_prompt_file'] == 'legacy.txt'
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize('fields,expected_model,locked', [
    ({}, 'legacy', True),
    ({'desensitize': {'model': 'current'}}, 'current', False),
    ({'desensitize': {}}, 'global', False),
])
def test_orchestrator_direct_config_legacy_and_new_empty_priority(
        fake_engines, fields, expected_model, locked):
    config = {'api': {'key': 'offline-key', 'model': 'global',
                      'generation_params': {'temperature': 0.7, 'top_p': 0.9},
                      'harmonize': {'model': 'legacy', 'generation_params': {'temperature': 0.0}},
                      **copy.deepcopy(fields)}}
    before = copy.deepcopy(config)
    translation_engine = SimpleNamespace()
    ctx = SimpleNamespace(config=config, thread_lock=threading.Lock(),
                          translate_engine=translation_engine, _desensitize_engine=None,
                          pr=SimpleNamespace(debug_enabled=False), pricing={})

    engine = orch._get_desensitize_engine(ctx)
    assert orch._get_desensitize_engine(ctx) is engine
    if fields.get('desensitize') == {}:
        assert engine is translation_engine
        assert fake_engines == []
    else:
        assert fake_engines == [engine]
        assert engine.config['model'] == expected_model
        assert engine.config['generation_params'] == {
            'temperature': 0.0 if locked else 0.7, 'top_p': 0.9}
        assert engine._purpose_temp_locked is locked
    assert config == before


@pytest.mark.parametrize('selection,expected', [
    ('legacy', '旧规则。'), ('current', '新规则。'), ('empty', None),
])
def test_orchestrator_direct_prompt_legacy_and_new_empty_priority(tmp_path, selection, expected):
    old_path = tmp_path / 'legacy.txt'
    new_path = tmp_path / 'current.txt'
    old_path.write_text('旧规则。', encoding='utf-8')
    new_path.write_text('新规则。', encoding='utf-8')
    prompts = {'harmonize_system_prompt_file': str(old_path)}
    if selection != 'legacy':
        prompts['desensitize_system_prompt_file'] = '' if selection == 'empty' else str(new_path)
    ctx = SimpleNamespace(config={'prompts': prompts})
    before = copy.deepcopy(ctx.config)

    assert orch._load_desensitize_prompt(ctx) == expected
    assert ctx.config == before


@pytest.mark.parametrize('fields,expected_key,temperature,locked', [
    ({}, 'legacy-key', 0.0, True),
    ({'desensitize': {}}, 'global-key', 0.7, False),
    ({'desensitize': {'api_key': 'current-key'}}, 'current-key', 0.7, False),
    ({'desensitize': {'key': 'current-key', 'generation_params': {'temperature': 0.2}}},
     'current-key', 0.2, True),
])
def test_prepare_purpose_config_priority_and_legacy_temperature_lock(
        fake_engines, fields, expected_key, temperature, locked):
    config = {'api': {'key': 'global-key', 'model': 'global',
                      'generation_params': {'temperature': 0.7, 'top_p': 0.9},
                      'models': {'global': {'flags': ['local']}},
                      'harmonize': {'key': 'legacy-key',
                                   'generation_params': {'temperature': 0.0}},
                      **copy.deepcopy(fields)}}
    before = copy.deepcopy(config)

    merged = prep._purpose_config(config, 'desensitize')
    assert merged['key'] == expected_key
    assert merged['generation_params'] == {'temperature': temperature, 'top_p': 0.9}
    assert not {'desensitize', 'harmonize', 'worldview'} & merged.keys()
    engine = prep._purpose_engine(SimpleNamespace(config=config, pricing={}), 'desensitize')
    assert fake_engines == [engine]
    assert engine.config == merged
    assert engine._purpose_temp_locked is locked
    merged['models']['global']['flags'].append('changed')
    merged['generation_params']['top_p'] = 0.5
    assert config == before


def test_legacy_failure_log_purpose_appends_to_desensitize_log(tmp_path):
    fail = {'reason': '本地测试失败', 'raw': '合成响应'}
    orch._write_worldview_failure_log(tmp_path, fail, purpose='harmonize')
    orch._write_worldview_failure_log(tmp_path, fail, purpose='desensitize')

    log_dir = tmp_path / 'log'
    assert {p.name for p in log_dir.iterdir()} == {'desensitize.log'}
    content = (log_dir / 'desensitize.log').read_text(encoding='utf-8')
    assert content.count('世界观脱敏失败') == 2
    assert content.count('本地测试失败') == 2
    assert content.count('合成响应') == 2


def test_token_tracker_legacy_request_type_uses_desensitize_label():
    tracker = TokenTracker()
    for purpose in ('harmonize', 'desensitize'):
        tracker.record(TokenUsage(request_type=purpose, label='合成世界观', work_key='synthetic',
                                  prompt_tokens=3, hit_tokens=1, miss_tokens=2,
                                  completion_tokens=4, cost=0.0, elapsed=0.1))
    assert tracker.type_counts == {'脱敏': 2}
    assert tracker.total_api_calls == 2
    assert tracker.total_prompt_tokens == 6
    assert tracker.total_completion_tokens == 8

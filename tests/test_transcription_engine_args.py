# -*- coding: utf-8 -*-
"""extra_args 引擎配置键分流测试：不调用 infer.exe。"""
import json
from pathlib import Path

import pytest

from pipeline.orchestrator import (
    _load_generation_config_file,
    _prepare_engine_extra_args,
    _split_engine_extra_args,
)


SIX_ARGS = [
    '--smart_split_with_vad=true',
    '--vad_filter=true',
    '--vad_threshold=0.2',
    '--vad_min_speech_duration_ms=100',
    '--no_speech_threshold=0.85',
    '--beam_size=5',
]


def test_split_six_args():
    cli, overrides, config_path = _split_engine_extra_args(SIX_ARGS)
    assert cli == ['--smart_split_with_vad=true', '--vad_threshold=0.2',
                   '--vad_min_speech_duration_ms=100']
    assert overrides == {'vad_filter': True, 'no_speech_threshold': 0.85, 'beam_size': 5}
    assert config_path == ''


def test_split_space_form():
    cli, overrides, _ = _split_engine_extra_args(['--beam_size', '5', '--vad_filter', 'false'])
    assert cli == []
    assert overrides == {'beam_size': 5, 'vad_filter': False}


def test_split_invalid_value():
    with pytest.raises(ValueError):
        _split_engine_extra_args(['--vad_filter=maybe'])
    with pytest.raises(ValueError):
        _split_engine_extra_args(['--beam_size=5.5'])
    with pytest.raises(ValueError):
        _split_engine_extra_args(['--beam_size'])


def test_prepare_merges_engine_config(tmp_path):
    base = tmp_path / 'generation_config.json5'
    base.write_text('''{
        // 原有设置必须保留
        "task": "translate",
        "segment_merge": {"enabled": true, "max_gap_ms": 500,},
        "note": "http://example.com/a//b",  /* 注释 */
        "beam_size": 1,
    }''', encoding='utf-8')
    cli, tmp = _prepare_engine_extra_args(SIX_ARGS, tmp_path)
    assert cli == ['--smart_split_with_vad=true', '--vad_threshold=0.2',
                   '--vad_min_speech_duration_ms=100']
    assert tmp is not None and tmp.exists()
    merged = json.loads(tmp.read_text(encoding='utf-8'))
    assert merged['task'] == 'translate'
    assert merged['segment_merge'] == {'enabled': True, 'max_gap_ms': 500}
    assert merged['note'] == 'http://example.com/a//b'
    assert merged['vad_filter'] is True
    assert merged['no_speech_threshold'] == 0.85
    assert merged['beam_size'] == 5
    tmp.unlink()


def test_prepare_without_base_file(tmp_path):
    cli, tmp = _prepare_engine_extra_args(['--vad_filter=true'], tmp_path)
    assert cli == []
    assert tmp is not None
    assert json.loads(tmp.read_text(encoding='utf-8')) == {'vad_filter': True}
    tmp.unlink()


def test_prepare_without_engine_keys_keeps_cli(tmp_path):
    cli, tmp = _prepare_engine_extra_args(
        ['--smart_split_with_vad=true', '--generation_config', 'custom.json5'], tmp_path)
    assert cli == ['--smart_split_with_vad=true', '--generation_config', 'custom.json5']
    assert tmp is None


def test_prepare_uses_user_generation_config(tmp_path):
    custom = tmp_path / 'custom.json5'
    custom.write_text('{"task": "transcribe", "beam_size": 2}', encoding='utf-8')
    _, tmp = _prepare_engine_extra_args(
        ['--generation_config=custom.json5', '--beam_size=5'], tmp_path)
    merged = json.loads(tmp.read_text(encoding='utf-8'))
    assert merged == {'task': 'transcribe', 'beam_size': 5}
    tmp.unlink()


def test_complex_json5_raises(tmp_path):
    bad = tmp_path / 'bad.json5'
    bad.write_text("{'task': 'translate'}", encoding='utf-8')
    with pytest.raises(ValueError):
        _load_generation_config_file(bad)

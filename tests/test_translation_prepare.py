# -*- coding: utf-8 -*-
"""准备模式离线契约测试：只用临时合成字幕、假引擎，禁止 socket。"""
from __future__ import annotations

import copy
import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pipeline import translation_prepare as prep


class FakeEngine:
    def build_system_prompt(self, **context):
        return 'SYSTEM ' + json.dumps(context, ensure_ascii=False)

    def build_user_prompt(self, lines, **context):
        return 'USER ' + json.dumps({'lines': lines, **context}, ensure_ascii=False)

    def build_polish_prompt(self, lines, **context):
        return 'POLISH ' + json.dumps(context, ensure_ascii=False), 'before {"lines": []} after'


@pytest.fixture(autouse=True)
def no_socket(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('离线准备测试禁止网络连接')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def source(work, name='a', content='[ti:synthetic]\n[00:01.00]一行目\n[00:02.00]\n[00:03.00]二行目\n[00:04.00]三行目\n'):
    path = work / f'{name}.ja.lrc'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def empty_context():
    return {'worldview': None, 'terms': {}, 'alias': []}


def context():
    return {'worldview': {'worldview': '25岁学生，兄妹关系，非自愿', 'scene': '',
                          'characters': [{'name': 'アキ', 'name_cn': '明', 'role': '学生',
                                          'relationship': '兄妹', 'personality': ''}],
                          'themes': [], 'special_terms': {'街': '城镇'}},
            'terms': {'街': '城镇'}, 'alias': ['听音参考']}


def export(work, tracks, **kwargs):
    return prep.export_prepared_work(work, tracks, kwargs.pop('original_context', empty_context()),
                                     kwargs.pop('normalized_context', empty_context()),
                                     kwargs.pop('scriptbook_map', {}),
                                     engine=kwargs.pop('engine', FakeEngine()),
                                     app_config=kwargs.pop('app_config', {}), **kwargs)


def read_manifest(work):
    return json.loads((work / prep.PACKAGE_DIR / 'manifest.json').read_text(encoding='utf-8'))


def make_ctx():
    return SimpleNamespace(config={'api': {'key': '', 'model': 'fake'}, 'app': {}}, api_cfg={}, pricing={})


def patch_run(monkeypatch):
    from pipeline import orchestrator as legacy
    from engines import translate_engine
    transcribe = Mock()
    monkeypatch.setattr(legacy, '_run_transcription_if_needed', transcribe)
    def collect(root, ctx):
        sources = {p.with_name(p.name[:-len('.ja.lrc')] + '.lrc'): p for p in root.rglob('*.ja.lrc')
                   if not p.with_name(p.name[:-len('.ja.lrc')] + '.lrc').exists()}
        return list(sources), sources, {}
    monkeypatch.setattr(legacy, '_collect_translation_pairs', collect)
    monkeypatch.setattr(prep, '_load_scriptbook_strict', lambda *a: {})
    monkeypatch.setattr(prep, '_original_context', lambda *a: empty_context())
    monkeypatch.setattr(prep, '_normalize_context', lambda value, ctx: copy.deepcopy(value))
    monkeypatch.setattr(translate_engine, 'create_translate_engine', lambda *a, **kw: FakeEngine())
    for name in ('_preflight_api_check', '_run_streaming_pipeline', '_wait_for_offpeak_if_needed', 'translate_one_lrc'):
        monkeypatch.setattr(legacy, name, Mock(side_effect=AssertionError(name)))
    return transcribe


def test_export_complete_assets_indices_chunks_and_polish(tmp_path, monkeypatch):
    work = tmp_path / 'RJ000001'
    path = source(work, 'nested/a')
    path.with_name('a.mp4').write_bytes(b'synthetic')
    aligned = {0: {'sb': '台本一', 'conf': 1.0}, 2: {'sb': '台本三', 'conf': 0.9}}
    align = Mock(return_value=aligned)
    monkeypatch.setattr(prep, 'align_asr_scriptbook', align)
    manifest = export(work, {'nested/a': path}, scriptbook_map={'nested/a': ['台本一', '台本三']},
                      app_config={'lrc_max_lines_per_request': 2, 'polish_after_translate': True,
                                  'key': 'must-not-export', 'headers': {'secret': 'must-not-export'}},
                      input_fingerprint='source-config-rules')
    package = work / prep.PACKAGE_DIR
    assert manifest['status'] == 'READY'
    assert Path(manifest['work_root']).is_absolute()
    assert manifest['tracks'][0]['target_lrc'] == 'nested/a.lrc'
    asset = json.loads((package / manifest['tracks'][0]['asset']).read_text(encoding='utf-8'))
    assert asset['texts_eff'] == ['一行目', '二行目', '三行目']
    assert asset['lyric_indices'] == [0, 2, 3]
    assert asset['time_tags'] == ['[00:01.00]', '[00:03.00]', '[00:04.00]']
    assert asset['chunks'] == [[0, 1], [2]]
    assert asset['aligned_eff']['2']['sb'] == '台本三'
    assert asset['polish_after_translate'] is True
    assert asset['output'] == {'srt_vtt_resync': True, 'has_video': True}
    align.assert_called_once_with(['一行目', '二行目', '三行目'], ['台本一', '台本三'])
    for variant in ('original', 'normalized'):
        prompts = asset['prompts'][variant]
        assert len(prompts) == 2
        last = json.loads((package / prompts[-1]).read_text(encoding='utf-8'))
        payload = json.loads(last['user_prompt'].removeprefix('USER '))
        assert payload['lines'] == ['0001: 三行目']
        assert payload['scriptbook_aligned'] == {'0': aligned[2]}
        template = json.loads((package / asset['polish_templates'][variant]).read_text(encoding='utf-8'))
        draft = json.dumps({'lines': [{'index': 1, 'text': '草稿'}]}, ensure_ascii=False)
        assert template['user_prefix'] + draft + template['user_suffix'] == 'before ' + draft + ' after'
    for relative, digest in manifest['assets'].items():
        assert not Path(relative).is_absolute() and '\\' not in relative
        assert hashlib.sha256((package / relative).read_bytes()).hexdigest() == digest
        assert b'must-not-export' not in (package / relative).read_bytes()
    assert prep._validate_package(work.resolve(), manifest, {'nested/a': path}, 'source-config-rules')
    assert not path.with_name('a.lrc').exists()


def test_empty_context_is_legal_and_skips_alignment(tmp_path, monkeypatch):
    path = source(tmp_path)
    align = Mock(side_effect=AssertionError('空台本不能对齐'))
    monkeypatch.setattr(prep, 'align_asr_scriptbook', align)
    assert export(tmp_path, {'a': path})['status'] == 'READY'
    ctx = make_ctx()
    assert prep._normalize_context(empty_context(), ctx) == empty_context()


@pytest.mark.parametrize('failure', ['parse', 'align', 'prompt', 'empty'])
def test_export_failure_never_ready_and_retains_old_data(tmp_path, monkeypatch, failure):
    path = source(tmp_path)
    old = export(tmp_path, {'a': path})
    kwargs = {}
    if failure == 'parse':
        monkeypatch.setattr(prep, 'parse_subtitle_file', lambda _: None)
    elif failure == 'align':
        monkeypatch.setattr(prep, 'align_asr_scriptbook', Mock(side_effect=RuntimeError('offline')))
        kwargs['scriptbook_map'] = {'a': ['参考']}
    elif failure == 'prompt':
        engine = FakeEngine()
        engine.build_user_prompt = Mock(side_effect=RuntimeError('offline'))
        kwargs['engine'] = engine
    else:
        path.write_text('[00:01.00]  \n', encoding='utf-8')
    with pytest.raises((ValueError, RuntimeError)):
        export(tmp_path, {'a': path}, **kwargs)
    assert read_manifest(tmp_path)['status'] == 'FAILED'
    assert all((tmp_path / prep.PACKAGE_DIR / p).exists() for p in old['assets'])


def test_prepare_multiroot_overlap_dedup_exact_targets_and_reuse(tmp_path, monkeypatch):
    transcribe = patch_run(monkeypatch)
    work = tmp_path / 'RJ000001'
    selected = work / 'selected'
    path = source(selected)
    source(work, 'not-selected')
    other = tmp_path / 'RJ000002'
    source(other, 'b')
    events = []
    ctx = make_ctx()
    result = prep.run_prepare_only([selected, selected / '..' / 'selected', other], ctx, events.append)
    assert result['prepared'] == 2 and result['failed'] == 0
    assert transcribe.call_count == 2
    assert read_manifest(work)['tracks'][0]['source_ja'] == 'selected/a.ja.lrc'
    assert len(read_manifest(work)['tracks']) == 1
    assert len(events) == 6 and all(isinstance(line, str) for line in events)
    assert 'api_calls' not in result
    assert any('READY manifest=' in line for line in events)
    assert prep.run_prepare_only([selected, other], ctx)['reused'] == 2
    path.write_text(path.read_text(encoding='utf-8') + '[00:05.00]四行目\n', encoding='utf-8')
    changed = prep.run_prepare_only([selected, other], ctx)
    assert changed['prepared'] == 1 and changed['reused'] == 1
    ctx.config['app']['lrc_max_lines_per_request'] = 1
    assert prep.run_prepare_only([selected], ctx)['prepared'] == 1
    manifest = read_manifest(work)
    corrupt = work / prep.PACKAGE_DIR / manifest['contexts']['normalized']
    corrupt.write_text('{}', encoding='utf-8')
    assert prep.run_prepare_only([selected], ctx)['prepared'] == 1


def test_overlapping_roots_transcribe_once_and_invalid_roots_before_io(tmp_path, monkeypatch):
    transcribe = patch_run(monkeypatch)
    work = tmp_path / 'RJ000001'
    child = work / 'nested'
    source(child)
    ctx = make_ctx()
    assert prep.run_prepare_only([child, work], ctx)['prepared'] == 1
    transcribe.assert_called_once_with(work.resolve(), ctx)
    transcribe.reset_mock()
    for roots in ([work, tmp_path / 'missing'], [source(tmp_path, 'file')], []):
        with pytest.raises(ValueError):
            prep.run_prepare_only(roots, ctx)
    transcribe.assert_not_called()
    with pytest.raises(TypeError, match='progress_callback'):
        prep.run_prepare_only([work], ctx, 'not callable')


def test_pending_audio_prevents_ready_even_with_prepared_tracks(tmp_path, monkeypatch):
    patch_run(monkeypatch)
    work = tmp_path / 'RJ000001'
    source(work)
    (work / 'missing.wav').write_bytes(b'synthetic')
    result = prep.run_prepare_only([work], make_ctx())
    assert result['failed'] == 1 and result['prepared'] == 0
    assert read_manifest(work)['status'] == 'FAILED'
    assert not (work / prep.PACKAGE_DIR / 'data').exists()


def test_source_outside_work_and_no_tracks_fail(tmp_path):
    work = tmp_path / 'work'
    path = source(tmp_path)
    with pytest.raises(ValueError):
        export(work, {'a': path})
    assert read_manifest(work)['status'] == 'FAILED'
    with pytest.raises(ValueError):
        export(work, {})
    assert read_manifest(work)['status'] == 'FAILED'


def test_console_reports_manifest_and_summary_without_callback(tmp_path, monkeypatch):
    from pipeline import orchestrator as legacy
    patch_run(monkeypatch)
    source(tmp_path)
    log = Mock()
    monkeypatch.setattr(legacy, '_log', log)
    result = prep.run_prepare_only([tmp_path], make_ctx())
    output = '\n'.join(call.args[0] for call in log.call_args_list)
    assert '[准备模式]' in output and 'prepared=1 reused=0 failed=0' in output
    assert f'READY manifest={tmp_path / prep.PACKAGE_DIR / "manifest.json"}' in output
    assert 'api_calls' not in result


def test_input_change_during_preparation_fails_with_safe_reason(tmp_path, monkeypatch):
    patch_run(monkeypatch)
    path = source(tmp_path)
    def change_input(value, ctx):
        path.write_text('[00:01.00]changed\n', encoding='utf-8')
        return value
    monkeypatch.setattr(prep, '_normalize_context', change_input)
    result = prep.run_prepare_only([tmp_path], make_ctx())
    assert result['failed'] == 1
    manifest = read_manifest(tmp_path)
    assert manifest['stage'] == 'inputs'
    assert manifest['reason_code'] == 'INPUT_CHANGED_DURING_PREPARATION'


def test_callback_exception_does_not_fail_preparation(tmp_path, monkeypatch):
    patch_run(monkeypatch)
    source(tmp_path)
    result = prep.run_prepare_only([tmp_path], make_ctx(), Mock(side_effect=RuntimeError('ui')))
    assert result['prepared'] == 1 and read_manifest(tmp_path)['status'] == 'READY'


def test_run_failure_records_failed_and_continues(tmp_path, monkeypatch):
    patch_run(monkeypatch)
    first, second = tmp_path / 'RJ000001', tmp_path / 'RJ000002'
    source(first)
    source(second)
    def original(work, *args):
        assert read_manifest(work)['status'] == 'PREPARING'
        if work == first:
            raise ValueError('invalid cached data')
        return empty_context()
    monkeypatch.setattr(prep, '_original_context', original)
    result = prep.run_prepare_only([first, second], make_ctx())
    assert result['failed'] == result['prepared'] == 1
    assert read_manifest(first)['status'] == 'FAILED'
    assert read_manifest(second)['status'] == 'READY'


def test_purpose_specific_key_and_generation_no_shared_mutation(monkeypatch):
    from engines import translate_engine
    config = {'api': {'key': '', 'model': 'global', 'generation_params': {'temperature': 0.7, 'top_p': 0.9},
                      'worldview': {'api_key': 'world-key', 'model': 'world',
                                    'generation_params': {'temperature': 0.2}},
                      'desensitize': {'key': 'desensitize-key', 'model': 'desensitize'}}}
    before = copy.deepcopy(config)
    created = []
    def create(api, **kwargs):
        created.append(api)
        return SimpleNamespace()
    monkeypatch.setattr(translate_engine, 'create_translate_engine', create)
    ctx = SimpleNamespace(config=config)
    prep._purpose_engine(ctx, 'worldview')
    prep._purpose_engine(ctx, 'desensitize')
    assert created[0]['key'] == 'world-key' and created[1]['key'] == 'desensitize-key'
    assert created[0]['generation_params'] == {'temperature': 0.2, 'top_p': 0.9}
    assert config == before
    with pytest.raises(ValueError, match='key'):
        prep._purpose_config({'api': {'key': ''}}, 'worldview')


@pytest.mark.parametrize('field,value', [('name', '別名'), ('name_cn', '小明'), ('role', '职员'),
                                        ('relationship', '朋友')])
def test_fidelity_character_fields_fail(field, value):
    original = context()
    normalized = copy.deepcopy(original)
    normalized['worldview']['characters'][0][field] = value
    with pytest.raises(ValueError, match='保真字段'):
        prep._validate_fidelity(original, normalized)


@pytest.mark.parametrize('text', ['年轻学生，兄妹关系，非自愿', '25岁学生，朋友关系，非自愿',
                                  '25岁学生，兄妹关系，自愿'])
def test_fidelity_age_relationship_consent_in_prose_fail(text):
    original = context()
    normalized = copy.deepcopy(original)
    normalized['worldview']['worldview'] = text
    with pytest.raises(ValueError):
        prep._validate_fidelity(original, normalized)


def test_fidelity_chinese_age_and_whitespace_only():
    original = empty_context()
    original['worldview'] = {'worldview': '十六岁学生'}
    changed = copy.deepcopy(original)
    changed['worldview']['worldview'] = '二十六岁学生'
    with pytest.raises(ValueError, match='非空白'):
        prep._validate_fidelity(original, changed)
    changed['worldview']['worldview'] = '  十六岁 学生\n'
    prep._validate_fidelity(original, changed)


def test_normalize_uses_new_rules_ignores_legacy_cache_and_rejects_model(tmp_path, monkeypatch):
    value = context()
    write_json(tmp_path / '脱敏版.worldview.json', {'worldview': '旧缓存不可复用'})
    seen = []
    def call(system, user):
        seen.append(system)
        return user, {}
    monkeypatch.setattr(prep, '_purpose_engine', lambda *a: SimpleNamespace(call_api=call))
    result = prep._normalize_context(value, make_ctx())
    assert result == value and seen == [prep.NORMALIZE_RULES]
    assert '年龄' in seen[0] and 'name_cn' in seen[0]
    for raw in ('我拒绝', '{"refusal":"policy"}', '{}'):
        monkeypatch.setattr(prep, '_purpose_engine', lambda *a, raw=raw: SimpleNamespace(call_api=lambda *a: (raw, {})))
        with pytest.raises((ValueError, TypeError)):
            prep._normalize_context(value, make_ctx())
    stale = export(tmp_path, {'a': source(tmp_path)})
    stale['rules_version'] = 'legacy-desensitize'
    assert not prep._validate_package(tmp_path, stale, {'a': tmp_path / 'a.ja.lrc'}, '')


@pytest.mark.parametrize('alias', [[1], [None], [{'a': []}], [["nested"]]])
def test_alias_bad_elements_rejected(alias):
    value = empty_context()
    value['alias'] = alias
    with pytest.raises(ValueError, match='alias'):
        prep._validate_context(value)


def test_terms_precedence_provenance_and_empty_cache_regenerates(tmp_path, monkeypatch):
    from engines import worldview_engine
    path = source(tmp_path)
    write_json(tmp_path / '.worldview.json', {})
    write_json(tmp_path / '.terms.json', {'共通': '作品译名'})
    write_json(tmp_path / '.alias.json', ['参考', {'误听': '正字'}])
    worldview = {'worldview': '', 'scene': '', 'characters': [{'name': '人名', 'name_cn': '角色名'}],
                 'special_terms': {'共通': '世界译名', '用语': '世界观译名'}}
    analyze = Mock(return_value=(worldview, {}, {}))
    monkeypatch.setattr(worldview_engine, 'analyze_worldview_with_llm', analyze)
    monkeypatch.setattr(prep, '_purpose_engine', lambda *a: object())
    ctx = make_ctx()
    ctx.config['terms'] = {'共通': '配置译名', '用语': '配置用语', '后补': '配置补缺'}
    result = prep._original_context(tmp_path, ctx, {'a': path})
    assert result['terms'] == {'共通': '作品译名', '人名': '角色名', '用语': '世界观译名', '后补': '配置补缺'}
    assert result['sources']['terms'] == {'共通': 'work', '人名': 'worldview.character',
                                         '用语': 'worldview.special_terms', '后补': 'config'}
    assert result['statuses']['worldview'] == 'generated'
    analyze.assert_called_once()


@pytest.mark.parametrize('mapping', [{'1': 'a'}, {'a': 'script.txt'}])
def test_scriptbook_presplit_both_index_and_named_mapping(tmp_path, monkeypatch, mapping):
    from pipeline import orchestrator as legacy
    path = source(tmp_path)
    script = tmp_path / 'script.txt'
    script.write_text('これは台本です', encoding='utf-8')
    ctx = make_ctx()
    ctx.config['api']['key'] = 'offline-key'
    ctx.api_cfg = ctx.config['api']
    monkeypatch.setattr(legacy, '_llm_identify_scriptbook_files', lambda *a: ([script], True, mapping, {}))
    assert prep._load_scriptbook_strict(tmp_path, ctx, {'a': path}) == {'a': ['これは台本です']}


def test_scriptbook_identification_disables_debug_only_on_local_copy(tmp_path, monkeypatch):
    from pipeline import orchestrator as legacy
    path = source(tmp_path)
    (tmp_path / 'script.txt').write_text('synthetic', encoding='utf-8')
    ctx = make_ctx()
    ctx.config['api']['key'] = 'offline-key'
    ctx.pr = SimpleNamespace(debug_enabled=True)
    def identify(candidates, work, local_ctx, names):
        assert local_ctx is not ctx and local_ctx.pr is not ctx.pr
        assert local_ctx.pr.debug_enabled is False
        return None
    monkeypatch.setattr(legacy, '_llm_identify_scriptbook_files', identify)
    assert prep._load_scriptbook_strict(tmp_path, ctx, {'a': path}) == {}
    assert ctx.pr.debug_enabled is True


def test_scriptbook_bad_cache_and_identification_failure_not_silent(tmp_path, monkeypatch):
    from pipeline import orchestrator as legacy
    path = source(tmp_path)
    cache = tmp_path / '_scriptbook_clean.json'
    write_json(cache, {'a': [1]})
    with pytest.raises(ValueError, match='缓存'):
        prep._load_scriptbook_strict(tmp_path, make_ctx(), {'a': path})
    # 同一缓存改成合法空台本，空行可合法，不触发对齐或 API。
    write_json(cache, {'a': []})
    assert prep._load_scriptbook_strict(tmp_path, make_ctx(), {'a': path}) == {'a': []}
    cache.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='缓存'):
        prep._load_scriptbook_strict(tmp_path, make_ctx(), {'a': path})
    # 改名使其不参与台本缓存；识别失败 [] 必须区别于明确无台本 None。
    cache.rename(tmp_path / 'old-cache.json')
    (tmp_path / 'script.txt').write_text('synthetic script', encoding='utf-8')
    ctx = make_ctx()
    ctx.config['api']['key'] = 'offline-key'
    monkeypatch.setattr(legacy, '_llm_identify_scriptbook_files', lambda *a: [])
    with pytest.raises(ValueError, match='识别请求失败'):
        prep._load_scriptbook_strict(tmp_path, ctx, {'a': path})
    monkeypatch.setattr(legacy, '_llm_identify_scriptbook_files', lambda *a: None)
    assert prep._load_scriptbook_strict(tmp_path, ctx, {'a': path}) == {}

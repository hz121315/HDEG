# -*- coding: utf-8 -*-
"""实际请求诊断沿引擎、失败分支至本地日志的离线回归。"""
from types import SimpleNamespace

import pytest

from engines.api_client import APIClient
from engines import translate_engine as te
from engines import worldview_engine as wv
from pipeline import orchestrator as orch
from tests.test_nsfw_fallback import make_ctx, FakeEngine, run_translate, ORIG_WV


def diagnostic(model='mock', attempt=1):
    return {'started_at': '2026-11-11T10:00:00+08:00',
            'ended_at': '2026-11-11T10:00:01+08:00', 'elapsed': 1.0,
            'requested_model': model, 'response_model': model + '-server',
            'protocol': 'openai', 'attempt': attempt,
            'settings': {'reasoning_effort': 'high', 'max_tokens': 16384}}


def engine():
    return te.OpenAICompatEngine({'model': 'mock', 'protocol': 'openai',
                                 'generation_params': {}}, verbose=False)


def test_call_api_passes_response_diagnostics(monkeypatch):
    e = engine()
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{}'), finish_reason='stop')],
        usage=None, request_diagnostics=[diagnostic()])
    monkeypatch.setattr(e._api, 'chat', lambda **kwargs: response)
    monkeypatch.setattr(APIClient, 'extract_token_stats', staticmethod(lambda usage: {}))
    _, stats = e.call_api('JSON', 'JSON')
    assert stats['request_diagnostics'] == [diagnostic()]


@pytest.mark.parametrize('retry_exception', [False, True])
def test_failed_internal_retry_keeps_both_diagnostics(monkeypatch, retry_exception):
    e = engine()
    e.config['generation_params']['auto_retry_on_empty'] = True
    calls = []

    def api(*args, **kwargs):
        calls.append(1)
        d = diagnostic('first' if len(calls) == 1 else 'retry')
        if retry_exception and len(calls) == 2:
            error = RuntimeError('mock failure')
            error.request_diagnostics = [d]
            raise error
        return 'not json', {'hit_tokens': 0, 'miss_tokens': 0, 'completion_tokens': 0,
                            'request_diagnostics': [d]}

    monkeypatch.setattr(e, 'call_api', api)
    result = e.translate_batch(['原文'] * 6, skip_hallucination_check=True)
    assert [d['requested_model'] for d in result['request_diagnostics']] == ['first', 'retry']


@pytest.mark.parametrize('api_exception', [False, True])
def test_translation_failure_log_contains_request_snapshot(tmp_path, api_exception):
    class Engine(FakeEngine):
        def translate_batch(self, *args, **kwargs):
            if api_exception:
                error = RuntimeError('mock failure')
                error.request_diagnostics = [diagnostic()]
                raise error
            result = super().translate_batch(*args, **kwargs)
            result['request_diagnostics'] = [diagnostic()]
            return result

    e = Engine([{'refuse': True}] * 2)
    run_translate(make_ctx(e), tmp_path)
    text = (tmp_path / 'log' / 'track1.log').read_text(encoding='utf-8')
    for expected in ('+08:00', 'mock-server', 'reasoning_effort', 'max_tokens', '块: 1'):
        assert expected in text


@pytest.mark.parametrize('purpose', ['worldview', 'harmonize'])
@pytest.mark.parametrize('api_exception', [False, True])
def test_worldview_failure_diagnostics_and_log(tmp_path, purpose, api_exception):
    e = engine()

    def api(*args, **kwargs):
        if api_exception:
            error = RuntimeError('mock failure')
            error.request_diagnostics = [diagnostic()]
            raise error
        return 'not json', {'request_diagnostics': [diagnostic()]}

    e.call_api = api
    if purpose == 'worldview':
        _, _, fail = wv.analyze_worldview_with_llm(e, ['样本'])
    else:
        _, stats = wv.harmonize_worldview_with_llm(e, ORIG_WV)
        fail = stats['failure_info']
    orch._write_worldview_failure_log(tmp_path, fail, purpose=purpose)
    log_file = tmp_path / 'log' / (purpose + '.log')
    text = log_file.read_text(encoding='utf-8')
    assert 'mock-server' in text and '+08:00' in text and 'reasoning_effort' in text
    orch._write_worldview_failure_log(tmp_path, fail, purpose=purpose)
    assert log_file.read_text(encoding='utf-8').count('请求记录 1') == 2


def test_ensure_harmonized_failure_writes_log(tmp_path):
    ctx = make_ctx(FakeEngine())
    e = engine()
    e.call_api = lambda *a, **k: ('not json', {'request_diagnostics': [diagnostic()]})
    ctx._harmonize_engine = e
    assert orch._ensure_harmonized_worldview(ctx, str(tmp_path), ORIG_WV) is None
    assert 'mock-server' in (tmp_path / 'log' / 'harmonize.log').read_text(encoding='utf-8')


def test_missing_diagnostics_not_guessed():
    assert '不可用' in orch._format_request_diagnostics([])[0]

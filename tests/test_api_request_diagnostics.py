# -*- coding: utf-8 -*-
"""API 请求诊断契约：全部 mock，不连接真实服务，也不改动重试/路由行为。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from engines import api_client as module
from engines.api_client import APIClient, _AnthropicAPIError, _CompatResponse


MESSAGES = [
    {'role': 'system', 'content': 'private-system-prompt'},
    {'role': 'user', 'content': 'private-user-message'},
]
PROTOCOLS = ('openai', 'anthropic', 'responses')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """封住实际网络出口；客户端和代理初始化均由各测试 mock。"""
    def forbidden(*args, **kwargs):
        pytest.fail('诊断测试不得发出真实网络请求')

    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr(APIClient, '_ensure_client', lambda self: None)
    monkeypatch.setattr(APIClient, '_proxy_session', staticmethod(forbidden))
    monkeypatch.setattr(module._time_mod, 'sleep', lambda seconds: None)


def test_real_openai_sdk_diagnostic_scope(monkeypatch):
    """真实 SDK 的内部重试包含在一条 SDK 调用记录内，响应允许挂载诊断属性。"""
    from openai import OpenAI
    try:
        import httpx
    except ImportError:
        import httpx2 as httpx

    sent = []

    def handle(request):
        sent.append(request)
        if len(sent) == 1:
            return httpx.Response(500, json={'error': {'message': 'temporary'}})
        return httpx.Response(200, json={
            'id': 'mock', 'object': 'chat.completion', 'created': 0, 'model': 'server-model',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'answer'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})

    client = make_client()
    with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
        with OpenAI(api_key='mock', base_url='https://mock.invalid/v1',
                    http_client=http_client, max_retries=1) as sdk:
            client._client = sdk
            result = client.chat(messages=MESSAGES, max_tokens=50, max_retries=1)
    assert len(sent) == 2
    assert len(result.request_diagnostics) == 1
    assert result.request_diagnostics[0]['scope'] == 'sdk_call'
    assert result.request_diagnostics[0]['response_model'] == 'server-model'


def make_client(protocol='openai', reasoning=None, **config):
    cfg = {
        'protocol': protocol,
        'model': 'requested-model',
        'key': 'private-api-key',
        'base_url': 'https://private.invalid/v1',
        'generation_params': {'temperature': 0.6, 'top_p': 0.8},
        'models': {'requested-model': {
            'protocol': protocol,
            'max_output_tokens': 120,
            'reasoning': reasoning or {},
        }},
    }
    cfg.update(config)
    return APIClient(cfg)


def http_response(protocol, *, model='server-model', **overrides):
    data = {'usage': {}}
    if model is not None:
        data['model'] = model
    if protocol == 'anthropic':
        data.update(content=[{'type': 'text', 'text': 'answer'}], stop_reason='end_turn')
    else:
        data.update(output=[{'type': 'message', 'content': [
            {'type': 'output_text', 'text': 'answer'},
        ]}], status='completed')
    data.update(overrides)
    return SimpleNamespace(status_code=200, json=lambda: deepcopy(data))


def install_sender(monkeypatch, client, sender):
    if client.protocol_name == 'openai':
        client._client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=sender),
        ))
    else:
        monkeypatch.setattr(client, '_proxy_session', lambda: SimpleNamespace(post=sender))


def assert_record(record, protocol, attempt=1, response_model=None,
                  requested_model='requested-model'):
    assert set(record) == {
        'started_at', 'ended_at', 'elapsed', 'requested_model',
        'response_model', 'protocol', 'attempt', 'settings', 'scope',
    }
    assert record['scope'] == ('sdk_call' if protocol == 'openai' else 'http_request')
    started = datetime.fromisoformat(record['started_at'])
    ended = datetime.fromisoformat(record['ended_at'])
    assert started.utcoffset() is not None
    assert ended.utcoffset() is not None
    assert ended >= started
    assert isinstance(record['elapsed'], float)
    assert record['elapsed'] >= 0
    assert record['requested_model'] == requested_model
    assert record['response_model'] == response_model
    assert record['protocol'] == protocol
    assert record['attempt'] == attempt
    assert 'private-' not in repr(record)


def test_openai_retry_records_final_filtered_settings(monkeypatch):
    client = make_client(reasoning={'style': 'effort'})
    response = _CompatResponse('answer', 1, 0, 1, 2, model='server-raw-name')
    sender = Mock(side_effect=[_AnthropicAPIError(400, 'unsupported'), response])
    install_sender(monkeypatch, client, sender)

    result = client.chat(messages=MESSAGES, max_tokens=500, reasoning_effort='high',
                         json_mode=True, max_retries=2)

    first, second = result.request_diagnostics
    assert_record(first, 'openai')
    assert_record(second, 'openai', 2, 'server-raw-name')
    assert first['settings'] == {
        'max_tokens': 120, 'temperature': 0.6, 'top_p': 0.8,
        'reasoning_effort': 'high', 'response_format': {'type': 'json_object'},
    }
    assert second['settings'] == {'max_tokens': 120, 'temperature': 0.6, 'top_p': 0.8}
    assert sender.call_count == 2
    assert sender.call_args_list[0].kwargs['reasoning_effort'] == 'high'
    assert 'reasoning_effort' not in sender.call_args_list[1].kwargs
    assert result.model == 'server-raw-name'
    assert result.choices[0].message.content == 'answer'


def test_openai_extra_override_and_nested_whitelist(monkeypatch):
    extra = {
        'temperature': 0.25,
        'thinking': {'type': 'enabled', 'budget_tokens': 4096, 'key': 'private-nested-key'},
        'reasoning': {'effort': 'low', 'enabled': True, 'prompt': 'private-prompt'},
        'response_format': {'type': 'json_schema', 'json_schema': {'private-schema': 'secret'}},
        'enable_thinking': True,
        'headers': {'Authorization': 'private-auth'},
        'messages': MESSAGES,
        'key': 'private-extra-key',
        'url': 'https://private.invalid',
        'arbitrary': 'private-arbitrary',
        'thinking_budget': 'private-invalid-budget',
    }
    client = make_client(reasoning={
        'style': 'effort', 'param': 'reasoning.effort',
        'thinking_switch': True, 'enable': extra,
    })
    sender = Mock(return_value=SimpleNamespace(model='server-name'))
    install_sender(monkeypatch, client, sender)
    result = client.chat(messages=MESSAGES, max_tokens=100, reasoning_effort='high')
    record = result.request_diagnostics[0]

    assert_record(record, 'openai', response_model='server-name')
    assert record['settings'] == {
        'max_tokens': 100, 'temperature': 0.25, 'top_p': 0.8,
        'thinking': {'type': 'enabled', 'budget_tokens': 4096},
        'reasoning': {'effort': 'low', 'enabled': True},
        'enable_thinking': True, 'response_format': {'type': 'json_schema'},
    }
    # 仅过滤诊断，不改发送参数；快照不引用随后可能变化的 extra_body。
    assert sender.call_args.kwargs['extra_body']['key'] == 'private-extra-key'
    sender.call_args.kwargs['extra_body']['thinking']['budget_tokens'] = 1
    assert record['settings']['thinking']['budget_tokens'] == 4096


@pytest.mark.parametrize('path', ['arbitrary.secret', 'reasoning.api_key', 'reasoning.effort'])
def test_dynamic_reasoning_cannot_log_arbitrary_fields_or_secret_values(monkeypatch, path):
    client = make_client(reasoning={'style': 'effort', 'param': path})
    sender = Mock(return_value=SimpleNamespace())
    install_sender(monkeypatch, client, sender)
    result = client.chat(messages=MESSAGES, max_tokens=100,
                         reasoning_effort='private-secret-effort')
    assert_record(result.request_diagnostics[0], 'openai')
    assert result.request_diagnostics[0]['settings'] == {
        'max_tokens': 100, 'temperature': 0.6, 'top_p': 0.8,
    }


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_network_errors_finalize_all_attempts_on_runtime_error(monkeypatch, protocol):
    client = make_client(protocol)
    sender = Mock(side_effect=ConnectionError('disconnected'))
    install_sender(monkeypatch, client, sender)
    with pytest.raises(RuntimeError) as caught:
        client.chat(messages=MESSAGES, max_tokens=100, max_retries=2)
    records = caught.value.request_diagnostics
    assert len(records) == sender.call_count == 2
    for attempt, record in enumerate(records, 1):
        assert_record(record, protocol, attempt)
    assert records[0]['settings'] == records[1]['settings']
    assert records[0]['settings'] is not records[1]['settings']


@pytest.mark.parametrize('protocol', ('anthropic', 'responses'))
def test_http_final_body_and_stripped_retry(monkeypatch, protocol):
    spec = ({'style': 'budget', 'budgets': {'high': 2048}}
            if protocol == 'anthropic'
            else {'style': 'effort', 'strip_sampling_params': True})
    client = make_client(protocol, reasoning=spec)
    rejected = SimpleNamespace(status_code=400, text='unsupported')
    sender = Mock(side_effect=[rejected, http_response(protocol, model='server-raw-name')])
    install_sender(monkeypatch, client, sender)
    result = client.chat(messages=MESSAGES, max_tokens=500, reasoning_effort='high',
                         max_retries=2)
    first, second = result.request_diagnostics
    assert_record(first, protocol)
    assert_record(second, protocol, 2, 'server-raw-name')
    if protocol == 'anthropic':
        assert first['settings'] == {
            'max_tokens': 120, 'temperature': 0.6, 'top_p': 0.8,
            'thinking': {'type': 'enabled', 'budget_tokens': 2048},
        }
        assert second['settings'] == {'max_tokens': 120, 'temperature': 0.6}
    else:
        # Responses 原有输出放大和采样剔除必须如实反映，不能改参数逻辑。
        assert first['settings'] == {
            'max_output_tokens': 32768, 'reasoning': {'effort': 'high'},
        }
        assert second['settings'] == {'max_output_tokens': 32768, 'temperature': 0.6}
    for call, record in zip(sender.call_args_list, (first, second)):
        body = call.kwargs['json']
        for name, value in record['settings'].items():
            assert body[name] == value
    assert result.model == 'server-raw-name'
    assert result.choices[0].message.content == 'answer'


def test_anthropic_effort_output_config(monkeypatch):
    client = make_client('anthropic', reasoning={
        'effort_param': {'anthropic': 'output_config.effort'},
    })
    sender = Mock(return_value=http_response('anthropic'))
    install_sender(monkeypatch, client, sender)
    result = client.chat(messages=MESSAGES, max_tokens=100, reasoning_effort='high')
    assert result.request_diagnostics[0]['settings']['output_config'] == {'effort': 'high'}
    assert sender.call_args.kwargs['json']['output_config'] == {'effort': 'high'}


@pytest.mark.parametrize('protocol', ('anthropic', 'responses'))
@pytest.mark.parametrize('failure', ('json', 'content'))
def test_http_parse_failure_still_closes_record(monkeypatch, protocol, failure):
    client = make_client(protocol)
    if failure == 'json':
        response = SimpleNamespace(status_code=200, json=Mock(side_effect=ValueError('bad json')))
        response_model = None
    else:
        response = http_response(protocol, content=[None], output=[None])
        response_model = 'server-model'
    install_sender(monkeypatch, client, Mock(return_value=response))
    with pytest.raises(RuntimeError) as caught:
        client.chat(messages=MESSAGES, max_tokens=100, max_retries=1)
    record, = caught.value.request_diagnostics
    assert_record(record, protocol, response_model=response_model)


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_missing_response_model_and_repeated_chats_are_isolated(monkeypatch, protocol):
    client = make_client(protocol)
    sender = Mock(side_effect=lambda *a, **k: (
        SimpleNamespace() if protocol == 'openai' else http_response(protocol, model=None)
    ))
    install_sender(monkeypatch, client, sender)
    first = client.chat(messages=MESSAGES, max_tokens=100, temperature=0.1)
    second = client.chat(messages=MESSAGES, max_tokens=100, temperature=0.9)
    assert first.request_diagnostics is not second.request_diagnostics
    assert len(first.request_diagnostics) == len(second.request_diagnostics) == 1
    assert_record(first.request_diagnostics[0], protocol)
    assert_record(second.request_diagnostics[0], protocol)
    assert first.request_diagnostics[0]['settings']['temperature'] == 0.1
    assert second.request_diagnostics[0]['settings']['temperature'] == 0.9
    assert getattr(second, 'model', None) is None


@pytest.mark.parametrize('exhausted', (False, True))
def test_attempts_continue_across_model_rotation(monkeypatch, exhausted):
    client = make_client(fallback_models=['fallback-model'])
    quota = _AnthropicAPIError(429, 'quota exhausted')
    sender = Mock(side_effect=[quota, quota if exhausted else SimpleNamespace(model='raw-fallback')])
    install_sender(monkeypatch, client, sender)
    if exhausted:
        with pytest.raises(RuntimeError) as caught:
            client.chat(messages=MESSAGES, max_tokens=500, max_retries=1)
        records = caught.value.request_diagnostics
    else:
        result = client.chat(messages=MESSAGES, max_tokens=500, max_retries=1)
        records = result.request_diagnostics
    assert len(records) == 2
    assert_record(records[0], 'openai')
    assert_record(records[1], 'openai', 2, None if exhausted else 'raw-fallback', 'fallback-model')
    assert records[0]['settings']['max_tokens'] == 120
    assert records[1]['settings']['max_tokens'] == 500


def test_initialization_failure_has_empty_diagnostics(monkeypatch):
    client = make_client()
    error = RuntimeError('initialization failed')
    monkeypatch.setattr(client, '_ensure_client', Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        client.chat(messages=MESSAGES, max_tokens=100)
    assert caught.value is error
    assert caught.value.request_diagnostics == []


@pytest.mark.parametrize('protocol', PROTOCOLS)
def test_concurrent_success_and_failure_do_not_share_records(monkeypatch, protocol):
    client = make_client(protocol)
    barrier = Barrier(2)

    def send(*args, **kwargs):
        params = kwargs if protocol == 'openai' else kwargs['json']
        temperature = params['temperature']
        # 两个调用的首次请求必须重叠；失败调用还要独立保留自己的第二次请求。
        if temperature == 0.9:
            barrier.wait(timeout=5)
        elif not getattr(send, 'failed_once', False):
            send.failed_once = True
            barrier.wait(timeout=5)
        if temperature == 0.1:
            raise ConnectionError('disconnected')
        return (SimpleNamespace(model='successful-model') if protocol == 'openai'
                else http_response(protocol, model='successful-model'))

    install_sender(monkeypatch, client, send)

    def chat(temperature):
        try:
            return client.chat(messages=MESSAGES, max_tokens=100, temperature=temperature,
                               max_retries=2)
        except RuntimeError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        failure_future = pool.submit(chat, 0.1)
        success_future = pool.submit(chat, 0.9)
        failure = failure_future.result(timeout=10)
        success = success_future.result(timeout=10)
    assert isinstance(failure, RuntimeError)
    assert not isinstance(success, Exception)
    failed_records = failure.request_diagnostics
    succeeded_records = success.request_diagnostics
    assert failed_records is not succeeded_records
    assert len(failed_records) == 2
    assert len(succeeded_records) == 1
    for attempt, record in enumerate(failed_records, 1):
        assert_record(record, protocol, attempt)
        assert record['settings']['temperature'] == 0.1
    assert_record(succeeded_records[0], protocol, response_model='successful-model')
    assert succeeded_records[0]['settings']['temperature'] == 0.9


def test_compat_response_model_is_optional_and_preserved():
    assert _CompatResponse('text', 1, 0, 1, 2).model is None
    response = _CompatResponse('text', 1, 0, 1, 2, model='raw-server-model')
    assert response.model == 'raw-server-model'
    assert response.usage.prompt_tokens == 1

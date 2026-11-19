# -*- coding: utf-8 -*-
"""准备开关必须在多根与旧翻译路径之前分流，默认不改变旧行为。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('stream,mode,roots_count', [(True, 'per_track', 1), (True, 'all_at_once', 2), (False, 'per_track', 2)])
def test_prepare_mode_routes_before_old_paths(tmp_path, monkeypatch, stream, mode, roots_count):
    from pipeline import orchestrator as orch
    from pipeline import translation_prepare as prep

    roots = [tmp_path / f'root-{i}' for i in range(roots_count)]
    for root in roots:
        root.mkdir()
    ctx = SimpleNamespace(app_cfg={'prepare_only': True}, pr=Mock())
    monkeypatch.setattr(orch, 'PipelineContext', lambda _: ctx)
    expected = {'prepared': 1, 'translated': 0}
    called = Mock(return_value=expected)
    monkeypatch.setattr(prep, 'run_prepare_only', called)
    for name in ('_run_streaming_pipeline', '_preflight_api_check', '_wait_for_offpeak_if_needed', 'translate_one_lrc'):
        monkeypatch.setattr(orch, name, Mock(side_effect=AssertionError('不得进入旧翻译路径')))
    progress = Mock()
    result = orch.run_pipeline(roots if roots_count > 1 else roots[0], progress_callback=progress)
    assert result is expected
    called.assert_called_once_with(roots, ctx, progress_callback=progress)


@pytest.mark.parametrize('app', [{}, {'prepare_only': False}])
def test_disabled_or_missing_switch_preserves_existing_stream_branch(tmp_path, monkeypatch, app):
    from pipeline import orchestrator as orch

    roots = [tmp_path / 'first', tmp_path / 'second']
    ctx = SimpleNamespace(app_cfg=app, config={'app': {}}, stream_workflow=True)
    monkeypatch.setattr(orch, 'PipelineContext', lambda _: ctx)
    old = Mock(return_value={'old_branch': True})
    monkeypatch.setattr(orch, '_run_streaming_pipeline', old)
    assert orch.run_pipeline(roots) == {'old_branch': True}
    old.assert_called_once_with(roots, ctx)


def test_prepare_switch_rejects_string_false(tmp_path, monkeypatch):
    from pipeline import orchestrator as orch

    ctx = SimpleNamespace(app_cfg={'prepare_only': 'false'})
    monkeypatch.setattr(orch, 'PipelineContext', lambda _: ctx)
    with pytest.raises(ValueError, match='prepare_only'):
        orch.run_pipeline(tmp_path)


def test_default_config_has_prepare_disabled(tmp_path):
    from io_adapter.config_loader import load_config

    assert load_config(tmp_path / 'empty.json')['app']['prepare_only'] is False

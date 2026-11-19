# -*- coding: utf-8 -*-
"""真实准备包导出与纯消费 CLI 的离线跨端契约。"""
from __future__ import annotations

import copy
import importlib.util
import json
import socket
from argparse import Namespace
from pathlib import Path

import pytest


SKILL = Path('C:/Users/Administrator/.gemini/config/skills/hz-rj-translate')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def consumer(tmp_path, monkeypatch):
    path = SKILL / 'scripts/hdeg_cli.py'
    if not path.is_file():
        pytest.skip('本机未安装纯消费 Skill')
    spec = importlib.util.spec_from_file_location('hdeg_handoff_consumer', path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    for name in ('RUNTIME_DIR', 'SESSIONS_ROOT', 'LOGS_ROOT'):
        monkeypatch.setattr(cli, name, tmp_path / name.lower())
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('离线契约不应联网'))
    return cli


@pytest.mark.parametrize('mode,polish', [('original', False), ('normalized', True)])
def test_real_export_to_consumer_two_chunks(tmp_path, monkeypatch, consumer, mode, polish):
    from pipeline import translation_prepare as prep
    from engines.translate_engine import OpenAICompatEngine

    work = tmp_path / 'RJ00000001'
    work.mkdir()
    source = work / '01.ja.lrc'
    source.write_text('[ti:旅行]\n[00:01.00]おはよう\n[00:02.00]\n[00:03.00]駅へ行こう\n[00:05.00]少し休もう\n[00:08.00]また明日\n', encoding='utf-8')
    original_bytes = source.read_bytes()
    context = {'worldview': {'worldview': '普通旅行', 'characters': []},
               'terms': {'駅': '车站'}, 'alias': []}
    # 使用真实提示词构造，仅替换读音依赖，不替换导出/消费函数。
    monkeypatch.setattr('engines.term_manager.build_clustered_terms_prompt_section', lambda terms, max_terms=30: json.dumps(terms, ensure_ascii=False))
    engine = OpenAICompatEngine({}, verbose=False)
    prep.export_prepared_work(work, {'01': source}, context, copy.deepcopy(context), {},
                              engine=engine, app_config={'lrc_max_lines_per_request': 2, 'polish_after_translate': polish, 'srt_vtt_resync': False})
    package = work / '.hdeg-translation'
    snapshot = {p.relative_to(package): p.read_bytes() for p in package.rglob('*') if p.is_file()}
    cli = consumer
    opened = cli.cmd_open_track(Namespace(work=str(work), track='01', context=mode, force=False), None, None, ROOT)
    assert opened['status'] == 'OPEN'
    assert opened['total_chunks'] == 2
    session = opened['session_id']
    translated = ['早上好', '去车站吧', '休息一下吧', '明天见']
    for chunk in (0, 1):
        payload = {'translations': [{'index': index + 1, 'text': text}
                                    for index, text in enumerate(translated[chunk * 2:chunk * 2 + 2])]}
        in_file = tmp_path / f'chunk-{chunk}.json'
        in_file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        result = cli.cmd_submit_chunk(Namespace(session=session, chunk=chunk, in_file=str(in_file), allow_echo=False, full_next_prompt=False, skip_polish=False), None, None, ROOT)
        if chunk == 0:
            assert result['status'] == 'NEXT_CHUNK'
    if polish:
        assert result['status'] == 'NEED_POLISH'
        in_file = tmp_path / 'polish.json'
        in_file.write_text('{"polish": {}}', encoding='utf-8')
        result = cli.cmd_submit_polish(Namespace(session=session, in_file=str(in_file)), None, None, ROOT)
    assert result['status'] == 'COMMITTED'
    output = (work / '01.lrc').read_text(encoding='utf-8')
    assert '[00:01.00]早上好' in output
    assert '[00:03.00]去车站吧' in output
    assert '[00:02.00]' not in output
    assert source.read_bytes() == original_bytes
    assert {p.relative_to(package): p.read_bytes() for p in package.rglob('*') if p.is_file()} == snapshot

# -*- coding: utf-8 -*-
"""脱敏命名迁移：仅使用中性临时缓存，不读取作品素材或调用 API。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from engines import worldview_engine as wv  # noqa: E402


@pytest.fixture
def neutral_worldview() -> dict:
    return {
        'worldview': '一座安静的小镇。',
        'characters': [{'name': '甲', 'name_cn': '甲', 'role': '图书管理员'}],
        'scene': '图书馆',
    }


def test_new_save_path_only(tmp_path, neutral_worldview):
    work_dir = tmp_path / 'work'
    assert wv.get_desensitized_worldview_path(work_dir) == work_dir / '脱敏版.worldview.json'

    wv.save_desensitized_worldview(work_dir, neutral_worldview)

    saved = work_dir / '脱敏版.worldview.json'
    assert json.loads(saved.read_text(encoding='utf-8')) == neutral_worldview
    assert not saved.read_bytes().startswith(b'\xef\xbb\xbf')
    assert not (work_dir / '和谐版.worldview.json').exists()
    assert not (work_dir / '.worldview.json').exists()
    assert wv.load_desensitized_worldview(work_dir) == neutral_worldview


def test_save_keeps_original_and_legacy_files(tmp_path, neutral_worldview):
    original = tmp_path / '.worldview.json'
    legacy = tmp_path / '和谐版.worldview.json'
    original.write_text('{"worldview": "原始缓存"}', encoding='utf-8')
    legacy.write_text('{"worldview": "旧缓存"}', encoding='utf-8')
    original_bytes = original.read_bytes()
    legacy_bytes = legacy.read_bytes()

    wv.save_desensitized_worldview(tmp_path, neutral_worldview)

    assert original.read_bytes() == original_bytes
    assert legacy.read_bytes() == legacy_bytes
    assert wv.load_desensitized_worldview(tmp_path) == neutral_worldview


def test_load_missing_cache(tmp_path):
    assert wv.load_desensitized_worldview(tmp_path) == {}
    assert list(tmp_path.iterdir()) == []


def test_load_legacy_only_when_new_absent(tmp_path, neutral_worldview):
    legacy = tmp_path / '和谐版.worldview.json'
    legacy.write_text(json.dumps(neutral_worldview, ensure_ascii=False), encoding='utf-8')
    legacy_bytes = legacy.read_bytes()

    assert wv.load_desensitized_worldview(tmp_path) == neutral_worldview
    assert legacy.read_bytes() == legacy_bytes
    assert not (tmp_path / '脱敏版.worldview.json').exists()


@pytest.mark.parametrize('new_worldview', [{'worldview': '新缓存'}, {}])
def test_load_new_cache_takes_priority(tmp_path, neutral_worldview, new_worldview):
    legacy = tmp_path / '和谐版.worldview.json'
    legacy.write_text(json.dumps(neutral_worldview, ensure_ascii=False), encoding='utf-8')
    wv.save_desensitized_worldview(tmp_path, new_worldview)

    assert wv.load_desensitized_worldview(tmp_path) == new_worldview
    assert json.loads(legacy.read_text(encoding='utf-8')) == neutral_worldview


@pytest.mark.parametrize('new_content', ['{broken', '', '[]', 'null'])
def test_bad_new_cache_does_not_fall_back(tmp_path, neutral_worldview, new_content):
    legacy = tmp_path / '和谐版.worldview.json'
    legacy.write_text(json.dumps(neutral_worldview, ensure_ascii=False), encoding='utf-8')
    new_cache = tmp_path / '脱敏版.worldview.json'
    new_cache.write_text(new_content, encoding='utf-8')

    assert wv.load_desensitized_worldview(tmp_path) == {}
    assert new_cache.read_text(encoding='utf-8') == new_content
    assert json.loads(legacy.read_text(encoding='utf-8')) == neutral_worldview


def test_legacy_imports_are_simple_aliases(tmp_path):
    from engines.worldview_engine import (
        HARMONIZE_OUTPUT_REQUIREMENTS,
        HARMONIZE_RULES_PROMPT,
        HARMONIZE_SYSTEM_PROMPT,
        get_harmonized_worldview_path,
        harmonize_with_llm,
        harmonize_worldview_with_llm,
        load_harmonized_worldview,
        save_harmonized_worldview,
    )

    for old, new in (
        (HARMONIZE_OUTPUT_REQUIREMENTS, wv.DESENSITIZE_OUTPUT_REQUIREMENTS),
        (HARMONIZE_RULES_PROMPT, wv.DESENSITIZE_RULES_PROMPT),
        (HARMONIZE_SYSTEM_PROMPT, wv.DESENSITIZE_SYSTEM_PROMPT),
        (get_harmonized_worldview_path, wv.get_desensitized_worldview_path),
        (load_harmonized_worldview, wv.load_desensitized_worldview),
        (save_harmonized_worldview, wv.save_desensitized_worldview),
        (harmonize_worldview_with_llm, wv.desensitize_worldview_with_llm),
        (harmonize_with_llm, wv.desensitize_worldview_with_llm),
    ):
        assert old is new

    assert get_harmonized_worldview_path(tmp_path) == tmp_path / '脱敏版.worldview.json'

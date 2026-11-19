# -*- coding: utf-8 -*-
"""独立准备入口：生成可校验的静态翻译资产，不执行字幕翻译。"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from io_adapter.lrc_handler import parse_subtitle_file
from engines.scriptbook_align import align_asr_scriptbook

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
RULES_VERSION = 'prepare-faithful-v1'
PACKAGE_DIR = '.hdeg-translation'
NORMALIZE_RULES = (
    '你是资料格式规范化编辑，只规范 JSON 格式与字符串空白，全部非空白字符必须逐字保留。'
    '不得改词、改写或改变任何事实。'
    '完整保留年龄、身份、职业、家庭及人物关系、同意或非自愿状态；不得成年化、'
    '替换亲属关系或将非自愿改为自愿。name、name_cn、术语及译名、alias 必须原样保留。'
    '保持全部 JSON 字段、类型、数组长度与顺序，允许空字符串、空数组、空对象和 null。'
    '无法保真时明确拒绝，不得伪造空数据。只返回输入同结构的完整 JSON 对象。'
)
_SECRET_KEYS = {'key', 'api_key', 'apikey', 'authorization', 'headers', 'password',
                'access_token', 'refresh_token', 'secret', 'token'}
_APP_KEYS = ('lrc_max_lines_per_request', 'polish_after_translate', 'srt_vtt_resync',
             'skip_hanhua_files', 'rj_lookup_depth')


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      indent=2).encode('utf-8')


def _digest(value) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path):
    # 缓存损坏必须向上传播，不能降为“没有资料”。
    return json.loads(path.read_text(encoding='utf-8'))


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    with temp.open('wb') as stream:
        stream.write(_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _safe_json(value) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in _SECRET_KEYS:
                raise ValueError('资产含非字符串键或凭据字段')
            _safe_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe_json(item)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError('资产含不可序列化数据')
    _json_bytes(value)


def _terms(value) -> dict[str, str]:
    if not isinstance(value, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError('术语必须为 dict[str,str]')
    return {k.strip(): v.strip() for k, v in value.items() if k.strip() and v.strip()}


def _validate_worldview(value) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError('worldview 必须是对象或 null')
    if value and (not {'worldview', 'characters', 'scene', 'themes', 'special_terms'} & value.keys()
                  or {'refusal', 'error', 'error_message'} & value.keys()):
        raise ValueError('worldview 不是设定资料或模型拒绝')
    for key in ('worldview', 'scene'):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f'worldview.{key} 必须是字符串')
    chars = value.get('characters', [])
    if not isinstance(chars, (dict, list)):
        raise ValueError('characters 必须为对象或数组')
    if isinstance(chars, dict) and any(not isinstance(v, (str, dict)) for v in chars.values()):
        raise ValueError('角色对象值格式错误')
    if isinstance(chars, list):
        for char in chars:
            if not isinstance(char, dict) or any(not isinstance(v, str) for k, v in char.items()
                                               if k in ('name', 'name_cn', 'role', 'relationship', 'personality')):
                raise ValueError('角色字段格式错误')
    if 'themes' in value and (not isinstance(value['themes'], list) or
                              any(not isinstance(x, str) for x in value['themes'])):
        raise ValueError('themes 必须为字符串数组')
    if 'special_terms' in value:
        _terms(value['special_terms'])
    _safe_json(value)


def _validate_context(value) -> None:
    if not isinstance(value, dict) or not {'worldview', 'terms', 'alias'} <= value.keys():
        raise ValueError('context 缺少 worldview/terms/alias')
    _validate_worldview(value['worldview'])
    _terms(value['terms'])
    if not isinstance(value['alias'], list) or any(
            not isinstance(item, str) and not (
                isinstance(item, dict) and all(isinstance(k, str) and isinstance(v, str)
                                              for k, v in item.items()))
            for item in value['alias']):
        raise ValueError('alias 元素必须是字符串或字符串映射')
    _safe_json(value)


def _validate_fidelity(original, normalized, field: str = '') -> None:
    """结构严格一致；事实字段、译名及散文中的敏感事实标记不可被弱化。"""
    if type(original) is not type(normalized):
        raise ValueError(f'规范化改变字段类型: {field}')
    protected = ('name', 'name_cn', 'terms', 'special_terms', 'alias', 'age', 'role',
                 'identity', 'relationship', 'consent', '年龄', '身份', '关系', '同意')
    if any(part in field.lower().split('.')[-1] for part in protected):
        if original != normalized:
            raise ValueError(f'规范化改变保真字段: {field}')
        return
    if isinstance(original, dict):
        if original.keys() != normalized.keys():
            raise ValueError(f'规范化改变字段集合: {field}')
        for key in original:
            _validate_fidelity(original[key], normalized[key], f'{field}.{key}')
    elif isinstance(original, list):
        if len(original) != len(normalized):
            raise ValueError(f'规范化改变数组长度: {field}')
        for left, right in zip(original, normalized):
            _validate_fidelity(left, right, field)
    elif isinstance(original, str):
        # 自由文本中的事实无法靠关键词穷举校验，本轮只接受空白规范化。
        if re.sub(r'\s+', '', original) != re.sub(r'\s+', '', normalized):
            raise ValueError(f'规范化改变非空白内容: {field}')
    elif original != normalized:
        raise ValueError(f'规范化改变事实值: {field}')


def _package_path(package: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or '..' in path.parts or '\\' in relative or ':' in relative:
        raise ValueError('准备包路径必须是相对 POSIX 路径')
    resolved = (package / path).resolve()
    resolved.relative_to(package.resolve())
    return resolved


def _state(work: Path, status: str, prepare_id: str, **metadata) -> dict:
    manifest = {'schema_version': SCHEMA_VERSION, 'status': status,
                'prepare_id': prepare_id, 'work_root': str(work.resolve()),
                'rules_version': RULES_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                **metadata}
    _write_json(work / PACKAGE_DIR / 'manifest.json', manifest)
    return manifest


def _prompt_settings(app_config: dict) -> dict:
    app = app_config.get('app', app_config)
    return {key: app[key] for key in _APP_KEYS if key in app}


def _validate_package(work: Path, manifest: dict, tracks: dict[str, Path], fingerprint: str) -> bool:
    """READY 只在范围、源摘要、规则摘要及每个消费资产均有效时可复用。"""
    try:
        if (manifest['schema_version'] != SCHEMA_VERSION or manifest['status'] != 'READY'
                or manifest['work_root'] != str(work.resolve())
                or manifest['rules_version'] != RULES_VERSION
                or manifest['rules_sha256'] != _digest(NORMALIZE_RULES)
                or manifest['input_fingerprint'] != fingerprint):
            return False
        package = work / PACKAGE_DIR
        assets = manifest['assets']
        if not isinstance(assets, dict) or not assets:
            return False
        for relative, sha in assets.items():
            if _sha(_package_path(package, relative)) != sha:
                return False
        references = list(manifest['contexts'].values())
        for ref in references:
            _validate_context(_read_json(_package_path(package, ref)))
        entries = manifest['tracks']
        if len(entries) != len(tracks) or {t['track_key'] for t in entries} != set(tracks):
            return False
        for entry in entries:
            source = tracks[entry['track_key']].resolve()
            if (entry['source_ja'] != source.relative_to(work).as_posix()
                    or entry['source_sha256'] != _sha(source)):
                return False
            asset = _read_json(_package_path(package, entry['asset']))
            if any(asset[k] != entry[k] for k in ('track_key', 'source_ja', 'target_lrc', 'source_sha256')):
                return False
            references.append(entry['asset'])
            for variant in ('original', 'normalized'):
                if len(asset['prompts'][variant]) != len(asset['chunks']):
                    return False
                references.extend(asset['prompts'][variant])
                references.append(asset['polish_templates'][variant])
                for ref in asset['prompts'][variant]:
                    prompt = _read_json(_package_path(package, ref))
                    if not all(isinstance(prompt[k], str) for k in ('system_prompt', 'user_prompt')):
                        return False
                template = _read_json(_package_path(package, asset['polish_templates'][variant]))
                if not all(isinstance(template[k], str) for k in ('system_prompt', 'user_prefix', 'user_suffix')):
                    return False
        return all(ref in assets for ref in references)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def export_prepared_work(work_dir, track_sources: dict[str, Path], original_context: dict,
                         normalized_context: dict, scriptbook_map: dict, *, engine,
                         app_config: dict, input_fingerprint: str = '') -> dict:
    """纯本地导出接口；track_sources 的 key 为作品内唯一音轨键，值必须在作品内。"""
    work = Path(work_dir).resolve()
    prepare_id = uuid4().hex
    _state(work, 'PREPARING', prepare_id)
    try:
        if not isinstance(track_sources, dict) or not track_sources:
            raise ValueError('准备作品至少需要一个待消费音轨')
        _validate_context(original_context)
        _validate_context(normalized_context)
        _validate_fidelity(original_context, normalized_context)
        if not isinstance(scriptbook_map, dict):
            raise ValueError('台本映射必须为对象')
        app = _prompt_settings(app_config)
        if not input_fingerprint:
            input_fingerprint = _digest({
                'sources': {key: _sha(Path(path)) for key, path in sorted(track_sources.items())},
                'original': original_context, 'normalized': normalized_context,
                'scriptbook': scriptbook_map, 'settings': app, 'rules': RULES_VERSION,
            })
        chunk_size = int(app.get('lrc_max_lines_per_request', 0) or 0)
        package = work / PACKAGE_DIR
        data = f'data/{prepare_id}'
        assets = {}

        def publish(relative, value):
            _safe_json(value)
            path = _package_path(package, relative)
            _write_json(path, value)
            assets[relative] = _sha(path)
            return relative

        contexts = {variant: publish(f'{data}/context.{variant}.json', context)
                    for variant, context in (('original', original_context), ('normalized', normalized_context))}
        templates = {}
        systems = {}
        for variant, context in (('original', original_context), ('normalized', normalized_context)):
            kwargs = {'terms': context['terms'], 'worldview': context['worldview'],
                      'alias_list': context['alias']}
            systems[variant] = engine.build_system_prompt(**kwargs)
            system, user = engine.build_polish_prompt([], **kwargs)
            marker = '{"lines": []}'
            if user.count(marker) != 1:
                raise ValueError('润色模板必须含唯一的空 lines JSON 占位')
            prefix, suffix = user.split(marker)
            templates[variant] = publish(f'{data}/polish.{variant}.json', {
                'system_prompt': system, 'user_prefix': prefix, 'user_suffix': suffix})
        entries = []
        seen_sources = set()
        for track_key, source_path in sorted(track_sources.items()):
            source = Path(source_path).resolve()
            relative = source.relative_to(work).as_posix()
            if not isinstance(track_key, str) or not track_key or not source.name.lower().endswith('.ja.lrc'):
                raise ValueError('音轨键不能为空且源必须为 .ja.lrc')
            if source in seen_sources:
                raise ValueError('重复源音轨')
            seen_sources.add(source)
            target = source.with_name(source.name[:-len('.ja.lrc')] + '.lrc')
            source_hash = _sha(source)
            sub = parse_subtitle_file(source)
            if sub is None:
                raise ValueError(f'无法解析源字幕: {relative}')
            if not (len(sub.original_lyrics) == len(sub.time_tags) == len(sub.lyric_indices)):
                raise ValueError('字幕解析结果索引不完整')
            effective = [i for i, text in enumerate(sub.original_lyrics) if text.strip()]
            texts = [sub.original_lyrics[i] for i in effective]
            if not texts:
                raise ValueError(f'源字幕没有有效歌词行: {relative}')
            lines = scriptbook_map.get(track_key, [])
            if not isinstance(lines, list) or any(not isinstance(x, str) for x in lines):
                raise ValueError('台本行必须是字符串数组')
            aligned = align_asr_scriptbook(texts, lines) if lines and texts else {}
            if not isinstance(aligned, dict) or any(type(i) is not int or not 0 <= i < len(texts)
                                                   or not isinstance(v, dict) for i, v in aligned.items()):
                raise ValueError('台本对齐结果不合法')
            size = chunk_size if chunk_size > 0 else max(len(texts), 1)
            chunks = [list(range(start, min(start + size, len(texts))))
                      for start in range(0, len(texts), size)]
            track_hash = hashlib.sha256(track_key.encode('utf-8')).hexdigest()
            prompts = {'original': [], 'normalized': []}
            for variant in prompts:
                for number, chunk in enumerate(chunks):
                    chunk_aligned = {i: aligned[index] for i, index in enumerate(chunk) if index in aligned}
                    user = engine.build_user_prompt(
                        [f'{i + 1:04d}: {texts[index]}' for i, index in enumerate(chunk)],
                        scriptbook_lines=lines, scriptbook_aligned=chunk_aligned or None)
                    ref = f'{data}/prompts/{track_hash}.{variant}.{number}.json'
                    prompts[variant].append(publish(ref, {'system_prompt': systems[variant], 'user_prompt': user}))
            entry = {'track_key': track_key, 'source_ja': relative,
                     'target_lrc': target.relative_to(work).as_posix(), 'source_sha256': source_hash,
                     'asset': f'{data}/tracks/{track_hash}.json'}
            publish(entry['asset'], {**{k: v for k, v in entry.items() if k != 'asset'},
                    'texts_eff': texts, 'time_tags': [sub.time_tags[i] for i in effective],
                    # consumer 用原歌词索引重建；不能使用 final_lines 的文件行索引。
                    'lyric_indices': effective, 'chunks': chunks, 'scriptbook_lines': lines,
                    'aligned_eff': {str(k): v for k, v in aligned.items()}, 'prompts': prompts,
                    'polish_templates': templates,
                    'polish_after_translate': bool(app.get('polish_after_translate', False)),
                    'output': {'srt_vtt_resync': bool(app.get('srt_vtt_resync', True)),
                               'has_video': any(target.with_suffix(ext).exists() for ext in
                                                ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'))}})
            entries.append(entry)
        manifest = {'schema_version': SCHEMA_VERSION, 'status': 'READY', 'prepare_id': prepare_id,
                    'work_root': str(work), 'rules_version': RULES_VERSION,
                    'rules_sha256': _digest(NORMALIZE_RULES),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'input_fingerprint': input_fingerprint, 'contexts': contexts,
                    'tracks': entries, 'assets': assets, 'settings': app}
        if not _validate_package(work, manifest, track_sources, input_fingerprint):
            raise ValueError('准备包完整性检查失败')
        _write_json(package / 'manifest.json', manifest)
        return manifest
    except Exception as exc:
        # 错误只存类型，API 异常字符串可能带端点凭据或请求头。
        _state(work, 'FAILED', prepare_id, error_type=type(exc).__name__,
               stage='export', reason_code='ASSET_EXPORT_FAILED')
        logger.error('准备包导出失败: %s (%s)', work, type(exc).__name__)
        raise


def _purpose_config(config: dict, purpose: str) -> dict:
    """用途覆盖只在本模块生效，检查合并后的当前用途 key，不修改共享 ctx。"""
    api = config.get('api', {}) or {}
    sub = api.get(purpose) if purpose in api else (
        api.get('harmonize') if purpose == 'desensitize' else {})
    sub = sub or {}
    if not isinstance(sub, dict):
        raise ValueError(f'api.{purpose} 必须为对象')
    merged = copy.deepcopy({k: v for k, v in api.items() if k not in ('desensitize', 'harmonize', 'worldview')})
    for key, value in sub.items():
        if key == 'generation_params':
            if not isinstance(value, dict):
                raise ValueError('generation_params 必须为对象')
            merged[key] = {**(merged.get(key) or {}), **value}
        elif value is not None and value != '':
            merged[key] = copy.deepcopy(value)
    # 子用途 api_key 与 key 都优先于全局同义字段。
    key = sub.get('key') or sub.get('api_key') or merged.get('key') or merged.get('api_key')
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f'api.{purpose} 当前用途未配置 key')
    merged['key'] = key
    return merged


def _purpose_engine(ctx, purpose):
    from engines.translate_engine import create_translate_engine
    config = _purpose_config(ctx.config, purpose)
    engine = create_translate_engine(config, verbose=False, pricing=getattr(ctx, 'pricing', None))
    api = ctx.config.get('api', {}) or {}
    sub = api.get(purpose) if purpose in api else (
        api.get('harmonize') if purpose == 'desensitize' else {})
    engine._purpose_temp_locked = 'temperature' in ((sub or {}).get('generation_params') or {})
    return engine


def _load_scriptbook_strict(work: Path, ctx, tracks: dict[str, Path]) -> dict:
    """沿用已有提取、识别和分割算法，但在准备模式禁止吞错回退为空。"""
    from pipeline import orchestrator as legacy
    from core.scriptbook_parser import collect_all_scriptbook_candidates, load_scriptbook_content
    from engines.scriptbook_cleaner import _conservative_pre_clean, ScriptbookSplitter
    names = list(tracks)
    for path in sorted(work.rglob('_scriptbook_clean.json')):
        if PACKAGE_DIR in path.parts or '_split_tracks' in path.parts:
            continue
        cached = _read_json(path)
        if not isinstance(cached, dict) or not cached:
            raise ValueError('台本缓存不是有效映射')
        for key, lines in cached.items():
            if not isinstance(key, str) or not isinstance(lines, list) or any(not isinstance(x, str) for x in lines):
                raise ValueError('台本缓存行格式错误')
        if set(names) & cached.keys():
            return {name: cached.get(name, []) for name in names}
    for directory in sorted(work.rglob('_split_tracks')):
        if PACKAGE_DIR in directory.parts or not directory.is_dir():
            continue
        files = sorted(directory.glob('*.txt'))
        if files:
            result = {}
            for name in names:
                exact = [f for f in files if f.stem == name.replace('/', '_') or f.stem == name]
                if len(exact) > 1:
                    raise ValueError('台本缓存音轨匹配不唯一')
                result[name] = exact[0].read_text(encoding='utf-8').splitlines() if exact else []
            if any(result.values()):
                return result
            raise ValueError('台本缓存无法匹配当前音轨')
    candidates = [p for p in collect_all_scriptbook_candidates(work) if PACKAGE_DIR not in p.parts]
    if not candidates:
        return {}
    _purpose_config(ctx.config, 'scriptbook')
    # 旧识别器 debug 会输出部分 key，只在局部副本关闭，不污染共享上下文。
    identify_ctx = copy.copy(ctx)
    if hasattr(ctx, 'pr'):
        identify_ctx.pr = copy.copy(ctx.pr)
        identify_ctx.pr.debug_enabled = False
    identified = legacy._llm_identify_scriptbook_files(candidates[:100], work, identify_ctx, names)
    if identified is None:
        return {}  # 模型明确确认无台本，区别于请求失败返回 []。
    if not isinstance(identified, tuple) or not identified or not identified[0]:
        raise ValueError('台本识别请求失败')
    files, pre_split, mapping, *rest = identified
    groups = rest[0] if rest else {}
    contents = {}
    for path in files:
        text = load_scriptbook_content(path, api_config=ctx.api_cfg)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('台本提取失败')
        contents[path.name] = [line.strip() for line in _conservative_pre_clean(text).splitlines() if line.strip()]
    result = {name: [] for name in names}
    if groups:
        for filename, group in groups.items():
            if filename not in contents or not isinstance(group, list):
                raise ValueError('台本多轨映射错误')
            for name in group:
                if name not in result:
                    raise ValueError('台本映射引用未知音轨')
                result[name] = contents[filename]
    elif pre_split and mapping:
        for key, value in mapping.items():
            # 旧识别接口历史数据含两种形态：文件列表的 1-based index→音轨，
            # 以及当前 prompt 要求的音轨→文件名；数字索引总是对原候选列表解析。
            if str(key).isdigit():
                index = int(key)
                if not 1 <= index <= len(candidates[:100]):
                    raise ValueError('台本文件索引越界')
                name, filename = value, candidates[index - 1].name
            else:
                name, filename = key, value
            if name not in result or filename not in contents:
                raise ValueError('台本映射引用未知音轨或文件')
            result[name] = contents[filename]
    else:
        raw = '\n'.join(line for lines in contents.values() for line in lines)
        samples = {name: '\n'.join((parse_subtitle_file(source).original_lyrics)[:15])
                   for name, source in tracks.items()}
        result, _ = ScriptbookSplitter(ctx.api_cfg, verbose=False).split_by_llm_line_range(
            names, raw, track_samples=samples)
    if not isinstance(result, dict) or not any(result.values()):
        raise ValueError('台本分割无有效结果')
    for name, lines in result.items():
        if name not in names or not isinstance(lines, list) or any(not isinstance(x, str) for x in lines):
            raise ValueError('台本分割结果错误')
    return result


def _original_context(work: Path, ctx, tracks: dict[str, Path]) -> dict:
    from engines.worldview_engine import analyze_worldview_with_llm, is_freetalk_context
    cache = work / '.worldview.json'
    if cache.exists():
        worldview = _read_json(cache)
        _validate_worldview(worldview)
        status = 'cached' if worldview else 'empty_cache'
    else:
        worldview, status = None, 'absent'
    if not worldview and all(is_freetalk_context(path)[0] for path in tracks.values()):
        worldview, status = None, 'freetalk'
    elif not worldview:
        samples = []
        for source in tracks.values():
            sub = parse_subtitle_file(source)
            if sub is None:
                raise ValueError('世界观采样字幕解析失败')
            samples.append('\n'.join(sub.original_lyrics[:40]))
        if not any(sample.strip() for sample in samples):
            worldview, status = {}, 'empty'
        else:
            worldview, _, fail = analyze_worldview_with_llm(
                _purpose_engine(ctx, 'worldview'), samples, verbose=False)
            if fail or not isinstance(worldview, dict):
                raise ValueError('世界观生成失败')
            _validate_worldview(worldview)
            status = 'generated'
    terms_path, alias_path = work / '.terms.json', work / '.alias.json'
    terms = _terms(_read_json(terms_path)) if terms_path.exists() else {}
    term_sources = {key: 'work' for key in terms}
    if worldview:
        chars = worldview.get('characters', [])
        if isinstance(chars, list):
            for char in chars:
                key, value = char.get('name', '').strip(), char.get('name_cn', '').strip()
                if key and value and key not in terms:
                    terms[key], term_sources[key] = value, 'worldview.character'
        for key, value in _terms(worldview.get('special_terms', {})).items():
            if key not in terms:
                terms[key], term_sources[key] = value, 'worldview.special_terms'
    for key, value in _terms(ctx.config.get('terms', {})).items():
        if key not in terms:
            terms[key], term_sources[key] = value, 'config'
    alias = _read_json(alias_path) if alias_path.exists() else []
    context = {'worldview': worldview, 'terms': terms, 'alias': alias,
               'sources': {'terms': term_sources},
               'statuses': {'worldview': status, 'alias': 'loaded' if alias_path.exists() else 'absent'}}
    _validate_context(context)
    return context


def _normalize_context(context: dict, ctx) -> dict:
    payload = {key: context[key] for key in ('worldview', 'terms', 'alias')}
    # 全空资料也是合法上下文，不为 null/空字段发无意义请求。
    if not any(payload.values()):
        return copy.deepcopy(context)
    raw, stats = _purpose_engine(ctx, 'desensitize').call_api(NORMALIZE_RULES,
                                                               json.dumps(payload, ensure_ascii=False))
    if stats.get('finish_reason') in ('length', 'content_filter', 'incomplete'):
        raise ValueError('规范化请求被截断或拒绝')
    normalized = json.loads(raw)
    _validate_context(normalized)
    _validate_fidelity(payload, normalized)
    return {**copy.deepcopy(context), **normalized}


def _input_fingerprint(work: Path, tracks: dict[str, Path], ctx) -> str:
    # 配置只取影响资料与提示词的白名单；凭据与 headers 不进入包或日志。
    api = ctx.config.get('api', {})
    public_fields = ('model', 'models', 'base_url', 'protocol', 'generation_params', 'timeout')
    public_api = {purpose: {k: cfg[k] for k in public_fields if k in cfg}
                  for purpose, cfg in [('default', api), ('worldview', api.get('worldview', {}) or {}),
                                       ('desensitize', (api.get('desensitize') if 'desensitize' in api
                                                       else api.get('harmonize')) or {})]}
    inputs = {}
    for path in work.rglob('*'):
        if not path.is_file() or PACKAGE_DIR in path.parts or 'translate_logs' in path.parts:
            continue
        if path.name in ('.worldview.json', '.terms.json', '.alias.json', '_scriptbook_clean.json') or path.suffix.lower() in ('.txt', '.pdf'):
            inputs[path.relative_to(work).as_posix()] = _sha(path)
    prompts = {}
    for key, value in (ctx.config.get('prompts', {}) or {}).items():
        if value:
            path = Path(value)
            if not path.is_file():
                from io_adapter.config_loader import get_app_dir
                path = get_app_dir() / value
            prompts[key] = _sha(path)
    from engines.translate_engine import load_builtin_translation_prompt
    return _digest({'sources': {key: [p.resolve().relative_to(work).as_posix(), _sha(p)]
                                for key, p in sorted(tracks.items())},
                    'inputs': inputs, 'api': public_api, 'settings': _prompt_settings(ctx.config),
                    'terms': ctx.config.get('terms', {}), 'prompts': prompts,
                    'builtin_prompt': load_builtin_translation_prompt(),
                    'rules': RULES_VERSION, 'rules_sha256': _digest(NORMALIZE_RULES)})


def run_prepare_only(roots, ctx, progress_callback=None) -> dict:
    """逐根转录、精确去重归组后准备；失败作品独立记录 FAILED 并继续其它作品。"""
    # orchestrator 在入口分流后才导入此模块，此处延迟反向导入避免循环。
    from pipeline import orchestrator as legacy
    from io_adapter.file_scanner import find_rj_work_root
    from engines.translate_engine import create_translate_engine
    roots = roots if isinstance(roots, (list, tuple)) else [roots]
    unique_roots = list(dict.fromkeys(Path(root).resolve() for root in roots))
    if not unique_roots or any(not root.is_dir() for root in unique_roots):
        raise ValueError('准备 roots 必须是存在的目录且不能为空')
    unique_roots = [root for root in unique_roots if not any(
        parent != root and root.is_relative_to(parent) for parent in unique_roots)]
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError('progress_callback 必须可调用，签名为 callback(line: str)')

    def emit(line: str) -> None:
        legacy._log(line, console=True)
        if progress_callback is not None:
            try:
                progress_callback(line)
            except Exception as exc:
                logger.warning('准备进度回调失败 (%s)', type(exc).__name__)

    emit('[准备模式] 生成静态翻译准备包')
    grouped = {}
    pending = set()
    seen = set()
    for root in unique_roots:
        legacy._run_transcription_if_needed(root, ctx)
        for key in legacy._groups_with_pending_audio(root, ctx):
            work = Path(key).resolve()
            pending.add(work)
            grouped.setdefault(work, {})
        targets, source_map, _ = legacy._collect_translation_pairs(root, ctx)
        for target in targets:
            target = Path(target).resolve()
            if target in seen:
                continue
            seen.add(target)
            # 不以 RJ 根重新扫描，防止局部目录准备扩展到未选中的其它轨。
            source = next((Path(src).resolve() for dst, src in source_map.items()
                           if Path(dst).resolve() == target), None)
            if source is None:
                raise ValueError('待准备目标缺少源字幕')
            source.relative_to(root)
            work, _ = find_rj_work_root(target, max_parent_depth=ctx.config.get('app', {}).get('rj_lookup_depth', 4))
            work = Path(work or root).resolve()
            grouped.setdefault(work, {})[legacy._track_key(work, source)] = source
    result = {'prepared': 0, 'failed': 0, 'reused': 0, 'works': [], 'translated': 0}
    for work, tracks in grouped.items():
        prepare_id = uuid4().hex
        prior_path = work / PACKAGE_DIR / 'manifest.json'
        stage, reason_code = 'inputs', 'INPUT_VALIDATION_FAILED'
        try:
            if work in pending:
                _state(work, 'PREPARING', prepare_id)
                reason_code = 'MISSING_TRANSCRIPTION_SOURCE'
                raise ValueError('所选范围仍有音频缺少转录源')
            fingerprint = _input_fingerprint(work, tracks, ctx)
            try:
                prior = _read_json(prior_path) if prior_path.exists() else {}
            except (ValueError, OSError):
                prior = {}
            if _validate_package(work, prior, tracks, fingerprint):
                result['reused'] += 1
                manifest = prior
            else:
                _state(work, 'PREPARING', prepare_id)
                emit(f'[{work.name}] PREPARING')
                stage, reason_code = 'scriptbook', 'SCRIPTBOOK_PREPARATION_FAILED'
                scriptbook = _load_scriptbook_strict(work, ctx, tracks)
                stage, reason_code = 'context', 'CONTEXT_PREPARATION_FAILED'
                original = _original_context(work, ctx, tracks)
                stage, reason_code = 'normalize', 'CONTEXT_NORMALIZATION_FAILED'
                normalized = _normalize_context(original, ctx)
                stage, reason_code = 'inputs', 'INPUT_CHANGED_DURING_PREPARATION'
                if _input_fingerprint(work, tracks, ctx) != fingerprint:
                    raise ValueError('准备期间源或设定发生变化')
                stage, reason_code = 'export', 'ASSET_EXPORT_FAILED'
                prompt_file = (ctx.config.get('prompts', {}) or {}).get('system_prompt_file')
                engine = create_translate_engine(ctx.config.get('api', {}), verbose=False,
                                                 system_prompt_file=prompt_file or None)
                manifest = export_prepared_work(work, tracks, original, normalized, scriptbook,
                                                engine=engine, app_config=ctx.config,
                                                input_fingerprint=fingerprint)
                result['prepared'] += 1
            result['works'].append(manifest)
            emit(f'[{work.name}] READY manifest={prior_path}')
        except Exception as exc:
            manifest = _state(work, 'FAILED', prepare_id, error_type=type(exc).__name__,
                              stage=stage, reason_code=reason_code)
            result['works'].append(manifest)
            result['failed'] += 1
            logger.error('作品准备失败: %s (%s/%s)', work, stage, reason_code)
            emit(f'[{work.name}] FAILED stage={stage} reason={reason_code} manifest={prior_path}')
    emit(f"[准备完成] prepared={result['prepared']} reused={result['reused']} failed={result['failed']}")
    return result

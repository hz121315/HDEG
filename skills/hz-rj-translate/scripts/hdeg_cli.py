# -*- coding: utf-8 -*-
"""只消费 HDEG 已准备包的本地 CLI；不准备资料、不调用模型或外部接口。

开轨校验生产包后，将上下文、完整分块提示词与输出计划冻结到技能 .runtime。
后续提交只读取会话快照，但每次检查日文源文件摘要；字幕只经安全提交发布。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_CONFIG_PATH = SKILL_DIR / "config.json"
RUNTIME_DIR = SKILL_DIR / ".runtime"
SESSIONS_ROOT = RUNTIME_DIR / "sessions"
LOGS_ROOT = RUNTIME_DIR / "translate_logs"
CONSUMER_SCHEMA_VERSION = 1
PACKAGE_SCHEMA_VERSION = 1
PACKAGE_DIR = ".hdeg-translation"
CONTEXT_MODES = ("original", "normalized")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
_ECHO_PUNCT = re.compile(r'''[\s　、。？?！!「」『』…—―～~♡♪・，．.,()（）\[\]【】:：;"'“”‘’]''')
_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]|\[\s*\d+:\d+(?:\.\d+)?\s*\]|\d+:\d{2}:\d{2}[,.]\d+\s*-->")


class CliError(RuntimeError):
    """可预期的参数、准备包或会话错误。"""


class PreparationError(CliError):
    def __init__(self, message: str, status: str = "FAILED"):
        super().__init__(message)
        self.status = status


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CliError(f"JSON 对象包含重复键: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes | str, label: str) -> Any:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise CliError(f"{label} 不是有效 UTF-8 JSON: {exc}") from exc


def _read_json(path: Path) -> Any:
    try:
        return _decode_json(path.read_bytes(), str(path))
    except OSError as exc:
        raise CliError(f"无法读取 JSON {path}: {exc}") from exc


def _load_consumer_hdeg_root() -> Path:
    """只使用技能配置的可信仓库位置，不读取任何 HDEG 全局配置。"""
    config = _read_json(SKILL_CONFIG_PATH)
    raw = config.get("hdeg_root") if isinstance(config, dict) else None
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise CliError("Skill config.json 必须提供绝对 hdeg_root")
    return Path(raw).resolve()


def _lrc_adapter(hdeg_root: Path):
    """仅从宿主配置的可信根导入纯字幕 IO，不设置或探测任何词典。"""
    root = Path(hdeg_root)
    if not root.is_absolute():
        raise CliError("hdeg_root 必须为可信绝对路径")
    root = root.resolve()
    expected = root / "io_adapter" / "lrc_handler.py"
    if not expected.is_file() or not expected.resolve().is_relative_to(root):
        raise CliError(f"可信 HDEG 根缺少纯字幕 IO: {expected}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # 不让只读消费在 HDEG 仓库创建 __pycache__。
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module = importlib.import_module("io_adapter.lrc_handler")
    finally:
        sys.dont_write_bytecode = previous
    if Path(module.__file__).resolve() != expected.resolve():
        raise CliError("已加载的 io_adapter.lrc_handler 不属于可信 HDEG 根")
    return module


def _relative_path(root: Path, raw: Any, label: str) -> Path:
    """生产包引用只能是规范 POSIX 相对路径；resolve 后再次限制边界。"""
    if (not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw
            or re.search(r"[\x00-\x1f\x7f]", raw)
            or any(part in ("", ".", "..") for part in raw.split("/"))):
        raise CliError(f"{label} 必须为安全的 POSIX 相对路径")
    relative = PurePosixPath(raw)
    if relative.is_absolute():
        raise CliError(f"{label} 不能为绝对路径")
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise CliError(f"{label} resolve 后超出允许目录")
    return path


def _work_path(work: Path, raw: Any, label: str) -> Path:
    path = _relative_path(work, raw, label)
    if path.resolve().is_relative_to((work / PACKAGE_DIR).resolve()):
        raise CliError(f"{label} 不能指向准备包")
    return path


def _file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CliError(f"无法读取文件摘要 {path}: {exc}") from exc


def _require_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CliError(f"{label} 必须为非空字符串")


def _require_sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CliError(f"{label} 必须为小写 SHA256")


def _track_key(work: Path, source: Path) -> str:
    rel = source.relative_to(work).as_posix()
    return rel[:-len(".ja.lrc")] if rel.endswith(".ja.lrc") else rel


def _validate_track_paths(work: Path, item: dict[str, Any]) -> tuple[Path, Path]:
    source = _work_path(work, item.get("source_ja"), "source_ja")
    target = _work_path(work, item.get("target_lrc"), "target_lrc")
    _require_string(item.get("track_key"), "track_key")
    if not source.name.endswith(".ja.lrc") or item["track_key"] != _track_key(work, source):
        raise CliError("track_key 与 source_ja 不匹配")
    if target != source.with_name(source.name[:-len(".ja.lrc")] + ".lrc"):
        raise CliError("target_lrc 必须为源字幕对应的同名 .lrc")
    if target.is_symlink():
        raise CliError("目标字幕不能是符号链接")
    _require_sha(item.get("source_sha256"), "source_sha256")
    return source, target


def _load_prepared_package(work: Path) -> dict[str, Any]:
    """先验证所有资产摘要，再解释引用；绝不从旧资料缓存补全准备包。"""
    package = work / PACKAGE_DIR
    manifest_path = package / "manifest.json"
    if not package.resolve().is_relative_to(work):
        raise PreparationError("准备包目录 resolve 后超出作品目录")
    if not manifest_path.exists():
        raise PreparationError(f"缺少准备包: {manifest_path}", "UNPREPARED")
    if not manifest_path.resolve().is_relative_to(package.resolve()):
        raise PreparationError("manifest.json resolve 后超出准备包")
    try:
        manifest_raw = manifest_path.read_bytes()
        manifest = _decode_json(manifest_raw, "manifest.json")
        if not isinstance(manifest, dict):
            raise CliError("manifest 顶层必须是对象")
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != PACKAGE_SCHEMA_VERSION:
            raise CliError("准备包 schema_version 不受支持，必须重新准备")
        if manifest.get("status") != "READY":
            raise CliError(f"准备包未就绪，status={manifest.get('status')}")
        for key in ("prepare_id", "rules_version", "created_at", "input_fingerprint"):
            _require_string(manifest.get(key), f"manifest.{key}")
        raw_root = manifest.get("work_root")
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute() or Path(raw_root).resolve() != work:
            raise CliError("manifest.work_root 与当前作品绝对路径不一致")
        asset_hashes = manifest.get("assets")
        if not isinstance(asset_hashes, dict) or not asset_hashes:
            raise CliError("manifest.assets 必须为非空对象")
        assets: dict[str, bytes] = {}
        for relative, digest in asset_hashes.items():
            asset_path = _relative_path(package, relative, "asset")
            if relative == "manifest.json":
                raise CliError("manifest 不能作为自身资产")
            _require_sha(digest, f"assets[{relative}]")
            if not asset_path.is_file():
                raise CliError(f"缺失准备资产: {relative}")
            content = asset_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise CliError(f"准备资产摘要不匹配: {relative}")
            assets[relative] = content

        def asset_json(relative: Any, label: str) -> dict[str, Any]:
            _relative_path(package, relative, label)
            if relative not in assets:
                raise CliError(f"{label} 引用了未登记资产: {relative}")
            data = _decode_json(assets[relative], label)
            if not isinstance(data, dict):
                raise CliError(f"{label} 必须为 JSON 对象")
            return data

        context_refs = manifest.get("contexts")
        if not isinstance(context_refs, dict) or set(context_refs) != set(CONTEXT_MODES):
            raise CliError("manifest.contexts 必须固定包含 original 和 normalized")
        contexts = {}
        for mode in CONTEXT_MODES:
            context = asset_json(context_refs[mode], f"context.{mode}")
            if ("worldview" not in context or context["worldview"] is not None and not isinstance(context["worldview"], dict)
                    or not isinstance(context.get("terms"), dict) or not isinstance(context.get("alias"), list)):
                raise CliError(f"context.{mode} 必须包含 worldview:dict|null、terms:dict、alias:list")
            contexts[mode] = context
        entries = manifest.get("tracks")
        if not isinstance(entries, list):
            raise CliError("manifest.tracks 必须为数组")
        tracks = []
        seen_keys, seen_sources, seen_targets = set(), set(), set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise CliError("manifest.tracks 每项必须为对象")
            source, target = _validate_track_paths(work, entry)
            if (entry["track_key"] in seen_keys or source.resolve() in seen_sources
                    or target.resolve() in seen_targets):
                raise CliError("manifest 包含重复音轨、源或目标")
            seen_keys.add(entry["track_key"])
            seen_sources.add(source.resolve())
            seen_targets.add(target.resolve())
            track = asset_json(entry.get("asset"), "track.asset")
            for key in ("track_key", "source_ja", "target_lrc", "source_sha256"):
                if track.get(key) != entry[key]:
                    raise CliError(f"track.{key} 与 manifest 不一致")
            texts, tags, indices, chunks = (track.get(key) for key in ("texts_eff", "time_tags", "lyric_indices", "chunks"))
            if not isinstance(texts, list) or not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
                raise CliError("track.texts_eff 必须为非空有效原文数组")
            if not isinstance(tags, list) or len(tags) != len(texts) or any(not isinstance(t, str) for t in tags):
                raise CliError("track.time_tags 必须与有效行一一对应")
            if (not isinstance(indices, list) or len(indices) != len(texts)
                    or any(type(i) is not int or i < 0 for i in indices) or indices != sorted(set(indices))):
                raise CliError("track.lyric_indices 必须为递增原文索引")
            if (not isinstance(chunks, list) or not chunks
                    or any(not isinstance(c, list) or not c or any(type(i) is not int for i in c) for c in chunks)
                    or [i for chunk in chunks for i in chunk] != list(range(len(texts)))):
                raise CliError("track.chunks 必须按顺序完整覆盖 0based 有效原文索引")
            sb, aligned = track.get("scriptbook_lines"), track.get("aligned_eff")
            if not isinstance(sb, list) or any(not isinstance(t, str) for t in sb):
                raise CliError("track.scriptbook_lines 必须为字符串数组")
            if not isinstance(aligned, dict) or any(
                    not re.fullmatch(r"0|[1-9][0-9]*", key) or int(key) >= len(texts) or not isinstance(value, dict)
                    for key, value in aligned.items()):
                raise CliError("track.aligned_eff 索引或类型无效")
            if type(track.get("polish_after_translate")) is not bool:
                raise CliError("track.polish_after_translate 必须为已固定 bool")
            output = track.get("output")
            if not isinstance(output, dict) or any(type(output.get(key)) is not bool for key in ("srt_vtt_resync", "has_video")):
                raise CliError("track.output 必须包含已固定的 srt_vtt_resync/has_video bool")
            prompt_refs, polish_refs = track.get("prompts"), track.get("polish_templates")
            if (not isinstance(prompt_refs, dict) or set(prompt_refs) != set(CONTEXT_MODES)
                    or not isinstance(polish_refs, dict) or set(polish_refs) != set(CONTEXT_MODES)):
                raise CliError("track 必须固定 original/normalized 提示词与润色模板")
            prompt_snapshots, polish_snapshots = {}, {}
            for mode in CONTEXT_MODES:
                refs = prompt_refs[mode]
                if not isinstance(refs, list) or len(refs) != len(chunks):
                    raise CliError(f"track.prompts.{mode} 数量与分块不一致")
                prompts = []
                for ref in refs:
                    prompt = asset_json(ref, f"prompt.{mode}")
                    for key in ("system_prompt", "user_prompt"):
                        _require_string(prompt.get(key), f"prompt.{mode}.{key}")
                    prompts.append(prompt)
                prompt_snapshots[mode] = prompts
                polish = asset_json(polish_refs[mode], f"polish.{mode}")
                if any(not isinstance(polish.get(key), str) for key in ("system_prompt", "user_prefix", "user_suffix")):
                    raise CliError("polish 模板必须包含 system_prompt/user_prefix/user_suffix 字符串")
                polish_snapshots[mode] = polish
            tracks.append({"data": track, "prompts": prompt_snapshots, "polish": polish_snapshots})
        return {"manifest": manifest, "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "package": package, "contexts": contexts, "tracks": tracks}
    except (CliError, OSError) as exc:
        raise PreparationError(f"准备包校验失败: {exc}") from exc


def _assert_package_unchanged(prepared: dict[str, Any]) -> None:
    package = prepared["package"]
    if _file_digest(package / "manifest.json") != prepared["manifest_sha256"]:
        raise CliError("开轨校验期间 manifest 发生变化，请重新开轨")
    for relative, digest in prepared["manifest"]["assets"].items():
        if _file_digest(_relative_path(package, relative, "asset")) != digest:
            raise CliError(f"开轨校验期间资产发生变化: {relative}")


def _validate_source(work: Path, track: dict[str, Any], adapter):
    source, _target = _validate_track_paths(work, track)
    if _file_digest(source) != track["source_sha256"]:
        raise CliError(f"日文源字幕摘要变化或文件缺失: {track['source_ja']}")
    sub = adapter.parse_subtitle_file(source)
    if _file_digest(source) != track["source_sha256"]:
        raise CliError("读取日文字幕期间源文件发生变化")
    if sub is None:
        raise CliError("日文源字幕无法解析，必须重新准备")
    # lyric_indices 是 original_lyrics 中的索引，不能误用 final_lines 的物理行索引。
    indices = [i for i, text in enumerate(sub.original_lyrics) if text and text.strip()]
    if ([sub.original_lyrics[i] for i in indices] != track["texts_eff"]
            or [sub.time_tags[i] for i in indices] != track["time_tags"] or indices != track["lyric_indices"]):
        raise CliError("准备轨的原文、时间标签或原文索引与源字幕不匹配")
    return sub


def _work_session_dir(work_dir: Path) -> tuple[str, Path]:
    resolved = work_dir.resolve()
    short_hash = hashlib.sha1(str(resolved).lower().encode("utf-8")).hexdigest()[:8]
    match = re.search(r"(RJ\d{6,})", resolved.name, re.IGNORECASE)
    prefix = match.group(1).upper() if match else re.sub(r'[\\/:*?"<>|\s]+', "_", resolved.name).strip("_")[:24] or "work"
    work_id = f"{prefix}_{short_hash}"
    sdir = SESSIONS_ROOT / work_id
    sdir.mkdir(parents=True, exist_ok=True)
    return work_id, sdir


def _atomic_write_json(path: Path, data: Any) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextlib.contextmanager
def _file_lock(path: Path):
    """操作系统释放进程锁；锁文件保留，避免删除后出现双锁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise CliError("同一任务正在由其他进程处理，请等待后再提交") from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CliError("同一任务正在由其他进程处理，请等待后再提交") from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _find_track_state_path(session_id: str) -> Path:
    if not re.fullmatch(r"[^/\\:*?\[\]{}<>|\x00-\x1f]{1,220}__[0-9a-f]{32}", session_id):
        raise CliError("非法音轨会话标识")
    matches = list(SESSIONS_ROOT.glob(f"*/tracks/{session_id}/state.json"))
    if len(matches) != 1:
        raise CliError(f"找不到唯一音轨会话: {session_id}")
    st_path = matches[0].resolve()
    if not st_path.is_relative_to(SESSIONS_ROOT.resolve()):
        raise CliError("音轨会话超出运行目录")
    return st_path


def _load_active_state(st_path: Path, allowed: tuple[str, ...] = ("OPEN",)) -> dict[str, Any]:
    state = _read_json(st_path)
    if not isinstance(state, dict) or type(state.get("consumer_schema_version")) is not int or state["consumer_schema_version"] != CONSUMER_SCHEMA_VERSION:
        raise CliError("旧会话缺少受支持的 consumer schema；禁止复用，请从准备包重新开轨")
    if state.get("session_id") != st_path.parent.name:
        raise CliError("会话标识与状态文件不一致")
    if state.get("status") not in allowed:
        raise CliError(f"当前会话状态 {state.get('status')} 不允许此操作")
    return state


def _state_paths(state: dict[str, Any]) -> tuple[Path, Path]:
    work = Path(state["work_dir"])
    source = _work_path(work, state["source_rel"], "session.source")
    target = _work_path(work, state["target_rel"], "session.target")
    if source.resolve() != Path(state["source_ja"]) or target.resolve() != Path(state["target_lrc"]) or target.is_symlink():
        raise CliError("会话源或目标路径发生变化")
    return source, target


def _assert_source_unchanged(state: dict[str, Any]) -> None:
    source, _target = _state_paths(state)
    if _file_digest(source) != state["source_sha256"]:
        raise CliError("日文源字幕已变化或被删除，请停止此会话并重新开轨")


def _locked_session(command):
    """串行化单会话读改写，锁不跨越宿主模型推理等待。"""
    from functools import wraps

    @wraps(command)
    def run(args, *config):
        st_path = _find_track_state_path(args.session)
        with _file_lock(st_path.with_suffix(".lock")):
            return command(args, *config)
    return run


def _scoped_work_roots(scope: Path) -> list[Path]:
    roots = set()
    if (scope / PACKAGE_DIR).exists():
        roots.add(scope)
    for package in scope.rglob(PACKAGE_DIR):
        if package.is_dir() and package.resolve().is_relative_to(scope):
            roots.add(package.parent.resolve())
    # 子目录只可上溯到所属 RJ 作品；不能越过 RJ 边界寻找别的作品包。
    for parent in scope.parents:
        if re.search(r"RJ\d{6,}", parent.name, re.IGNORECASE):
            if (parent / PACKAGE_DIR).exists():
                roots.add(parent)
            break
    for source in scope.rglob("*.ja.lrc"):
        if not source.resolve().is_relative_to(scope) or PACKAGE_DIR in source.relative_to(scope).parts:
            continue
        if any(source.is_relative_to(root) for root in roots):
            continue
        root = next((parent for parent in (source.parent, *source.parents) if parent.is_relative_to(scope)
                     and re.search(r"RJ\d{6,}", parent.name, re.IGNORECASE)), scope)
        roots.add(root)
    if not roots:
        roots.add(scope)
    return sorted(roots, key=str)


def cmd_scan(args: argparse.Namespace, _skill_cfg=None, _hdeg_cfg=None, hdeg_root=None) -> dict[str, Any]:
    scope = Path(args.path).resolve()
    if not scope.is_dir():
        raise CliError(f"扫描路径不是目录: {scope}")
    adapter = _lrc_adapter(hdeg_root)
    work_roots = _scoped_work_roots(scope)
    sources_by_work = {work: [] for work in work_roots}
    # READY 包可能只准备了局部目录，扫描覆盖范围必须由 scope 内源文件决定，不能由 manifest 决定。
    for source in sorted(scope.rglob("*.ja.lrc")):
        if (not source.is_file() or not source.resolve().is_relative_to(scope)
                or PACKAGE_DIR in source.relative_to(scope).parts):
            continue
        owners = [work for work in work_roots if source.is_relative_to(work)]
        if owners:
            sources_by_work[max(owners, key=lambda work: len(work.parts))].append(source)
    works = []
    for work in work_roots:
        info = {"work_dir": str(work), "preparation_status": "READY", "pending_tracks": [], "skipped_existing_lrc": 0}
        try:
            prepared = _load_prepared_package(work)
            prepared_sources = set()
            for entry in prepared["tracks"]:
                track = entry["data"]
                source, target = _validate_track_paths(work, track)
                if not source.resolve().is_relative_to(scope):
                    continue
                _validate_source(work, track, adapter)
                prepared_sources.add(source.resolve())
                if target.exists():
                    info["skipped_existing_lrc"] += 1
                else:
                    info["pending_tracks"].append({"track_key": track["track_key"], "source_ja": str(source.resolve()),
                                                   "target_lrc": str(target.resolve()), "has_video": track["output"]["has_video"],
                                                   "preparation_status": "READY"})
            for source in sources_by_work[work]:
                if source.resolve() in prepared_sources:
                    continue
                target = _work_path(work, source.relative_to(work).as_posix()[:-len(".ja.lrc")] + ".lrc", "target_lrc")
                if target.exists():
                    info["skipped_existing_lrc"] += 1
                    continue
                info["preparation_status"] = "UNPREPARED"
                info["reason"] = "scope 内存在未译源字幕，未被 READY 准备包 tracks 覆盖"
                info["pending_tracks"].append({"track_key": _track_key(work, source), "source_ja": str(source.resolve()),
                                               "target_lrc": str(target.resolve()), "preparation_status": "UNPREPARED"})
            _assert_package_unchanged(prepared)
            info["prepare_id"] = prepared["manifest"]["prepare_id"]
        except CliError as exc:
            info["preparation_status"] = exc.status if isinstance(exc, PreparationError) else "FAILED"
            info["reason"] = str(exc)
            info["pending_tracks"] = []
        works.append(info)
    statuses = [work["preparation_status"] for work in works]
    status = "FAILED" if "FAILED" in statuses else "UNPREPARED" if "UNPREPARED" in statuses else "READY"
    return {"status": status, "scan_root": str(scope), "total_works": len(works),
            "ready_works": statuses.count("READY"), "failed_works": statuses.count("FAILED"),
            "unprepared_works": statuses.count("UNPREPARED"),
            "pending_works": sum(bool(work["pending_tracks"]) for work in works),
            "total_pending_tracks": sum(len(work["pending_tracks"]) for work in works), "works": works}


def _select_track(prepared: dict[str, Any], work: Path, request: str) -> dict[str, Any]:
    if not isinstance(request, str) or not request:
        raise CliError("音轨键不能为空")
    candidate = Path(request)
    if candidate.is_absolute():
        if not candidate.resolve().is_relative_to(work):
            raise CliError("音轨源路径不在指定作品目录内")
        request = candidate.relative_to(work).as_posix()
    else:
        _relative_path(work, request, "track")
    exact = [item for item in prepared["tracks"] if request in (item["data"]["track_key"], item["data"]["source_ja"])]
    if len(exact) == 1:
        return exact[0]
    matches = [item for item in prepared["tracks"] if PurePosixPath(item["data"]["track_key"]).name == request]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CliError("音轨名存在歧义，请使用完整的作品内相对路径键")
    raise CliError(f"准备包未包含音轨: {request}")


def _render_prompt(prompt: dict[str, str]) -> str:
    return f"{prompt['system_prompt']}\n\n{'=' * 60}\n\n{prompt['user_prompt']}"


def cmd_open_track(args: argparse.Namespace, _skill_cfg=None, _hdeg_cfg=None, hdeg_root=None) -> dict[str, Any]:
    work = Path(args.work).resolve()
    if not work.is_dir():
        raise CliError(f"作品目录不存在: {work}")
    mode = args.context
    if mode not in CONTEXT_MODES:
        raise CliError("--context 只支持 original 或 normalized")
    prepared = _load_prepared_package(work)
    selected = _select_track(prepared, work, args.track)
    track = selected["data"]
    source, target = _validate_track_paths(work, track)
    _validate_source(work, track, _lrc_adapter(hdeg_root))
    _assert_package_unchanged(prepared)
    if target.exists() and not args.force:
        return {"status": "SKIPPED_EXISTS", "track_key": track["track_key"], "target_lrc": str(target.resolve())}
    target_digest = _file_digest(target)
    wid, sdir = _work_session_dir(work)
    safe_key = re.sub(r'[\\/:*?"<>|\s]+', "_", track["track_key"]).strip("_")[:80] or "track"
    session = f"{wid}__{safe_key}__{uuid.uuid4().hex}"
    session_dir = sdir / "tracks" / session
    context = prepared["contexts"][mode]
    state = {"consumer_schema_version": CONSUMER_SCHEMA_VERSION, "session_id": session, "work_id": wid,
             "work_dir": str(work), "track_key": track["track_key"], "source_ja": str(source.resolve()),
             "target_lrc": str(target.resolve()), "source_rel": track["source_ja"], "target_rel": track["target_lrc"],
             "context_mode": mode, "status": "OPEN", "source_sha256": track["source_sha256"],
             "target_digest_at_open": target_digest, "force_overwrite": bool(args.force), "opened_at": time.time(),
             "prepare_id": prepared["manifest"]["prepare_id"], "rules_version": prepared["manifest"]["rules_version"],
             "input_fingerprint": prepared["manifest"]["input_fingerprint"], "manifest_sha256": prepared["manifest_sha256"],
             "asset_hashes": prepared["manifest"]["assets"], "context_snapshot": context,
             "effective_worldview": context["worldview"], "effective_terms": context["terms"], "effective_alias": context["alias"],
             "texts_eff": track["texts_eff"], "time_tags": track["time_tags"], "lyric_indices": track["lyric_indices"],
             "chunks": track["chunks"], "scriptbook_lines": track["scriptbook_lines"], "aligned_eff": track["aligned_eff"],
             "chunk_prompts": selected["prompts"][mode], "polish_template": selected["polish"][mode],
             "polish_after_translate": track["polish_after_translate"], "output": track["output"], "translated_chunks": {}}
    prompt_file = session_dir / "chunk_0_prompt.md"
    _atomic_write_text(prompt_file, _render_prompt(state["chunk_prompts"][0]))
    _atomic_write_json(session_dir / "state.json", state)
    return {"status": "OPEN", "session_id": session, "track_key": track["track_key"], "context_mode": mode,
            "prepare_id": state["prepare_id"], "total_nonempty_lines": len(track["texts_eff"]),
            "scriptbook_lines_count": len(track["scriptbook_lines"]), "aligned_lines_count": len(track["aligned_eff"]),
            "total_chunks": len(track["chunks"]), "current_chunk": 0, "current_chunk_lines": len(track["chunks"][0]),
            "prompt_file": str(prompt_file)}


def _translation_error(text: str) -> str | None:
    if not text.strip() or text.strip() == "[EMPTY_LINE]":
        return "EMPTY_TRANSLATION"
    if _UNSAFE_TEXT.search(text):
        return "UNSAFE_TRANSLATION"
    return None


def _validate_and_extract_chunk_translations(raw_text: str, src_chunk_lines: list[str], allow_echo: bool) -> tuple[str, list[str], dict[str, Any]]:
    """严格验证本块 1..N ID 与文本；回声仅做本地正则归一化。"""
    expected = len(src_chunk_lines)
    indexed = {}
    try:
        obj = _decode_json(raw_text, "译文")
    except CliError as exc:
        return "INVALID_JSON", [], {"reason": str(exc)}
    if not isinstance(obj, dict) or not isinstance(obj.get("translations"), list):
        return "INVALID_SCHEMA", [], {"reason": "必须返回 translations 数组"}
    for item in obj["translations"]:
        if not isinstance(item, dict) or type(item.get("index")) is not int or not isinstance(item.get("text"), str):
            return "INVALID_SCHEMA", [], {"reason": "每项必须包含整数 index 和字符串 text"}
        index, text = item["index"], item["text"]
        if not 1 <= index <= expected:
            return "OUT_OF_RANGE", [], {"reason": f"index {index} 超出 1..{expected}"}
        if index in indexed:
            return "DUPLICATE_INDICES", [], {"reason": f"index {index} 重复"}
        error = _translation_error(text)
        if error:
            return error, [], {"reason": f"index {index} 为空译、包含换行、控制符或时间戳"}
        indexed[index] = text
    if not indexed:
        return "INVALID_SCHEMA", [], {"reason": "translations 为空"}
    missing = [i for i in range(1, expected + 1) if i not in indexed]
    if missing:
        return "MISSING_INDICES", [], {"expected_count": expected, "received_count": len(indexed), "missing_indices": missing[:30],
                                       "missing_sources": [{"index": i, "text": src_chunk_lines[i - 1]} for i in missing[:30]],
                                       "hint": "请补齐完整 1..N 数组后重新执行 submit-chunk"}
    ordered = [indexed[i] for i in range(1, expected + 1)]
    if not allow_echo:
        echoes = [{"index": i, "source": source, "echoed": translated}
                  for i, (source, translated) in enumerate(zip(src_chunk_lines, ordered), 1)
                  if _KANA.search(translated) and _ECHO_PUNCT.sub("", translated) == _ECHO_PUNCT.sub("", source)]
        if echoes:
            return "UNTRANSLATED_ECHO", [], {"echo_count": len(echoes), "echo_items": echoes[:20],
                                             "hint": "检测到日文原样复读，请翻译后重新提交；刻意保留可加 --allow-echo"}
    return "OK", ordered, {}


def _sync_lrc_to_srt_vtt(target: Path, translated: list[str], state: dict[str, Any], adapter) -> tuple[int, list[str], list[str]]:
    """本地小适配器：复用纯 parse/write，保留已有字幕时间轴并补齐视频字幕。"""
    synced, generated, warnings = 0, [], []
    work = Path(state["work_dir"])
    for ext, writer in ((".srt", adapter.write_srt_from_lrc), (".vtt", adapter.write_vtt_from_lrc)):
        staging = None
        try:
            other = _work_path(work, target.with_suffix(ext).relative_to(work).as_posix(), "衍生字幕")
            if other.is_symlink():
                raise CliError("衍生字幕不能是符号链接")
            before = _file_digest(other)
            sub = None
            if before is not None:
                sub = adapter.parse_subtitle_file(other)
                if sub is None or len(sub.original_lyrics) != len(translated):
                    warnings.append(f"{other.name} 无法解析或行数不匹配，未同步")
                    continue
            elif not state["output"]["has_video"]:
                continue
            fd, name = tempfile.mkstemp(prefix=f".{other.stem}.", suffix=ext, dir=other.parent)
            os.close(fd)
            staging = Path(name)
            if sub is not None:
                adapter.write_subtitle_file(sub, translated, staging)
            elif not writer(target, staging):
                raise CliError("无法从 LRC 生成衍生字幕")
            if _file_digest(other) != before or other.is_symlink():
                raise CliError("衍生字幕在同步期间变化，拒绝覆盖")
            if before is None:
                os.link(staging, other)
                generated.append(other.name)
            else:
                os.replace(staging, other)
                synced += 1
        except (OSError, CliError) as exc:
            warnings.append(f"LRC 已提交，{ext} 同步失败: {exc}")
        finally:
            if staging is not None and staging.exists():
                staging.unlink()
    for warning in warnings:
        _eprint(warning)
    return synced, generated, warnings


def _commit_completed_track(state: dict[str, Any], translated_texts: list[str], hdeg_root: Path) -> dict[str, Any]:
    """先验证快照，再发布完整目标；默认用硬链接原子拒绝覆盖。"""
    adapter = _lrc_adapter(hdeg_root)
    source, target = _state_paths(state)
    if len(translated_texts) != len(state["texts_eff"]) or any(not isinstance(t, str) or _translation_error(t) for t in translated_texts):
        raise CliError("整轨译文与开轨快照行数或文本安全要求不一致")
    lock_key = hashlib.sha256(str(target.resolve()).lower().encode("utf-8")).hexdigest()
    with _file_lock(RUNTIME_DIR / "target_locks" / f"{lock_key}.lock"):
        _assert_source_unchanged(state)
        expected = state["target_digest_at_open"]
        current = _file_digest(target)
        if current != expected:
            raise CliError("开轨后目标字幕已出现或发生变化，拒绝覆盖")
        if current is not None and not state["force_overwrite"]:
            raise CliError("目标字幕已存在，未获覆盖授权")
        sub = _validate_source(Path(state["work_dir"]), {**state, "source_ja": state["source_rel"], "target_lrc": state["target_rel"]}, adapter)
        fd, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".lrc", dir=target.parent)
        os.close(fd)
        staging = Path(name)
        try:
            adapter.write_subtitle_file_nonempty(sub, translated_texts, staging)
            _assert_source_unchanged(state)
            if _file_digest(target) != expected:
                raise CliError("写出前目标已变化，拒绝覆盖")
            if expected is None:
                try:
                    os.link(staging, target)
                except FileExistsError as exc:
                    raise CliError("其他任务已经生成目标，当前不会覆盖") from exc
                except OSError as exc:
                    raise CliError(f"目标文件系统不能安全发布无覆盖字幕: {exc}") from exc
            else:
                # --force 只授权开轨时记录的目标摘要；锁协调本技能写入者。
                os.replace(staging, target)
        finally:
            if staging.exists():
                staging.unlink()
        state["status"] = "COMMITTED"
        state["committed_at"] = time.time()
        _atomic_write_json(_find_track_state_path(state["session_id"]), state)
        synced, generated, warnings = 0, [], []
        if state["output"]["srt_vtt_resync"]:
            # LRC 已发布；衍生输出失败只报错，不把会话退回待翻译。
            try:
                synced, generated, warnings = _sync_lrc_to_srt_vtt(target, translated_texts, state, adapter)
            except Exception as exc:
                warnings.append(f"LRC 已提交，SRT/VTT 同步失败: {exc}")
                _eprint(warnings[-1])
    log_file = LOGS_ROOT / f"{state['work_id']}.log"
    elapsed = round(time.time() - state["opened_at"], 1)
    try:
        LOGS_ROOT.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as stream:
            stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [成功] {state['track_key']} | {state['context_mode']} | {len(translated_texts)} 行 | 耗时 {elapsed}s\n")
    except OSError as exc:
        warnings.append(f"LRC 已提交，摘要日志写入失败: {exc}")
        _eprint(warnings[-1])
    return {"status": "COMMITTED", "session_id": state["session_id"], "track_key": state["track_key"], "target_lrc": str(target),
            "context_mode": state["context_mode"], "lines_written": len(translated_texts), "synced_srt_vtt": synced,
            "generated_video_subs": generated, "work_log": str(log_file), "warnings": warnings}


@_locked_session
def cmd_submit_chunk(args: argparse.Namespace, _skill_cfg=None, _hdeg_cfg=None, hdeg_root=None) -> dict[str, Any]:
    st_path = _find_track_state_path(args.session)
    state = _load_active_state(st_path)
    _assert_source_unchanged(state)
    chunks, texts = state["chunks"], state["texts_eff"]
    chunk = args.chunk
    if type(chunk) is not int or not 0 <= chunk < len(chunks):
        raise CliError(f"--chunk 超出分块范围 0..{len(chunks) - 1}")
    expected = next((i for i in range(len(chunks)) if str(i) not in state["translated_chunks"]), None)
    if chunk != expected:
        raise CliError(f"当前只允许提交下一未完成块 {expected}，不能重写已接受的块")
    try:
        raw = Path(args.in_file).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"无法读取译文输入: {exc}") from exc
    status, ordered, detail = _validate_and_extract_chunk_translations(raw, [texts[i] for i in chunks[chunk]], bool(args.allow_echo))
    if status != "OK":
        return {"status": status, "session_id": args.session, "chunk": chunk, **detail}
    _assert_source_unchanged(state)
    state["translated_chunks"][str(chunk)] = ordered
    _atomic_write_json(st_path, state)
    following = chunk + 1
    if following < len(chunks):
        # 每块静态提示词本来完整；--full-next-prompt 保留为兼容无操作。
        prompt = _render_prompt(state["chunk_prompts"][following])
        prompt += "\n\n<previous_translation_data>\n" + json.dumps({"lines": ordered[-8:]}, ensure_ascii=False) + "\n</previous_translation_data>\n"
        prompt_file = st_path.parent / f"chunk_{following}_prompt.md"
        _atomic_write_text(prompt_file, prompt)
        return {"status": "NEXT_CHUNK", "session_id": args.session, "completed_chunk": chunk, "next_chunk": following,
                "total_chunks": len(chunks), "next_chunk_lines": len(chunks[following]), "prompt_file": str(prompt_file)}
    merged = [text for i in range(len(chunks)) for text in state["translated_chunks"][str(i)]]
    if state["polish_after_translate"] and not args.skip_polish:
        template = state["polish_template"]
        user = template["user_prefix"] + json.dumps({"lines": [{"index": i, "text": text} for i, text in enumerate(merged, 1)]}, ensure_ascii=False) + template["user_suffix"]
        prompt_file = st_path.parent / "polish_prompt.md"
        _atomic_write_text(prompt_file, _render_prompt({"system_prompt": template["system_prompt"], "user_prompt": user}))
        state["merged_draft"] = merged
        state["status"] = "NEED_POLISH"
        _atomic_write_json(st_path, state)
        return {"status": "NEED_POLISH", "session_id": args.session, "total_lines": len(merged), "prompt_file": str(prompt_file),
                "next_command_hint": f'将润色 JSON 写入临时文件后执行: submit-polish --session "{args.session}" --in <json_file>'}
    return _commit_completed_track(state, merged, hdeg_root)


@_locked_session
def cmd_submit_polish(args: argparse.Namespace, _skill_cfg=None, _hdeg_cfg=None, hdeg_root=None) -> dict[str, Any]:
    st_path = _find_track_state_path(args.session)
    state = _load_active_state(st_path, ("NEED_POLISH",))
    _assert_source_unchanged(state)
    draft = list(state.get("merged_draft") or [])
    if not draft:
        raise CliError("当前会话没有待润色草稿")
    data = _read_json(Path(args.in_file))
    if not isinstance(data, dict) or not isinstance(data.get("polish"), dict):
        raise CliError("润色结果必须包含 polish 对象，可用空对象保留原草稿")
    changed = 0
    for key, value in data["polish"].items():
        if not re.fullmatch(r"[1-9][0-9]*", key) or not isinstance(value, str) or _translation_error(value):
            raise CliError("润色键必须为正整数行号，值必须为非空单行字符串且不包含时间戳")
        pos = int(key) - 1
        if pos >= len(draft):
            raise CliError("润色行号超出草稿范围")
        if draft[pos].strip() != value.strip():
            draft[pos] = value.strip()
            changed += 1
    result = _commit_completed_track(state, draft, hdeg_root)
    result["polished_lines"] = changed
    return result


@_locked_session
def cmd_fail_track(args: argparse.Namespace, _skill_cfg=None, _hdeg_cfg=None, hdeg_root=None) -> dict[str, Any]:
    """显式停止会话；所有状态及失败日志仅写技能 .runtime。"""
    st_path = _find_track_state_path(args.session)
    state = _load_active_state(st_path, ("OPEN", "NEED_POLISH"))
    source, _target = _state_paths(state)
    # 停止也检查源摘要，但源变化不应阻止用户结束会话。
    state["source_unchanged_at_stop"] = _file_digest(source) == state["source_sha256"]
    parts = [f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [停止] {state['track_key']} | {state['context_mode']}", f"原因: {args.reason}"]
    if args.raw_file:
        parts.append("返回摘录:\n" + Path(args.raw_file).read_text(encoding="utf-8")[:4000])
    state["status"] = "FAILED_STOPPED"
    state["failure_reason"] = args.reason
    state["failed_at"] = time.time()
    _atomic_write_json(st_path, state)
    failure_log = st_path.parent / "failure.log"
    warnings = []
    for log_file, text in ((failure_log, "\n".join(parts)), (LOGS_ROOT / f"{state['work_id']}.log", parts[0] + f" | {args.reason}")):
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as stream:
                stream.write(text + "\n")
        except OSError as exc:
            warnings.append(f"停止状态已记录，日志写入失败: {exc}")
            _eprint(warnings[-1])
    return {"status": "FAILED_STOPPED", "session_id": args.session, "track_key": state["track_key"],
            "failure_log": str(failure_log), "reason": args.reason, "warnings": warnings}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HDEG 已准备翻译包的纯本地消费者")
    sub = parser.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("scan", help="只读扫描 scope 内及其 RJ 上游的准备包")
    scan.add_argument("path")
    opened = sub.add_parser("open-track", help="校验准备包并冻结指定音轨的消费会话")
    opened.add_argument("--work", required=True)
    opened.add_argument("--track", required=True)
    opened.add_argument("--context", choices=CONTEXT_MODES, default="original")
    opened.add_argument("--force", action="store_true", help="仅在用户明确授权后，覆写开轨时摘要未变的已有目标")
    submit = sub.add_parser("submit-chunk", help="严格校验分块译文并推进会话")
    submit.add_argument("--session", required=True)
    submit.add_argument("--chunk", type=int, required=True)
    submit.add_argument("--in", dest="in_file", required=True)
    submit.add_argument("--allow-echo", action="store_true")
    submit.add_argument("--full-next-prompt", action="store_true", help="兼容参数：静态提示词每块已完整，此参数不改变行为")
    submit.add_argument("--skip-polish", action="store_true")
    polish = sub.add_parser("submit-polish", help="校验稀疏润色结果并安全提交字幕")
    polish.add_argument("--session", required=True)
    polish.add_argument("--in", dest="in_file", required=True)
    fail = sub.add_parser("fail-track", help="登记失败并终止当前会话")
    fail.add_argument("--session", required=True)
    fail.add_argument("--reason", required=True)
    fail.add_argument("--raw-file", default="")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        hdeg_root = _load_consumer_hdeg_root()
        dispatch = {"scan": cmd_scan, "open-track": cmd_open_track, "submit-chunk": cmd_submit_chunk,
                    "submit-polish": cmd_submit_polish, "fail-track": cmd_fail_track}
        with contextlib.redirect_stdout(sys.stderr):
            result = dispatch[args.cmd](args, None, None, hdeg_root)
        _emit_json(result)
    except CliError as exc:
        _eprint(f"[错误] {exc}")
        _emit_json({"status": "ERROR", "error": str(exc)})
        sys.exit(1)
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        _emit_json({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"})
        sys.exit(1)


if __name__ == "__main__":
    main()

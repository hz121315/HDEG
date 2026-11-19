# -*- coding: utf-8 -*-
"""纯消费 CLI 契约测试：本地 manifest 夹具、网络与准备模块导入封锁。

运行：python -I -B -m unittest discover -s <本目录> -v
所有会话和作品均位于临时目录，不读取真实 .runtime、资料或接口配置。
"""
from __future__ import annotations

import argparse
import builtins
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

HDEG_ROOT = Path("C:/Users/Administrator/IdeaProjects/HDEG")
CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hdeg_cli.py"
FORBIDDEN_MODULES = ("engines", "core", "pipeline", "pyopenjtalk", "unidic", "unidic_lite", "MeCab", "fugashi", "requests", "httpx", "openai", "io_adapter.config_loader")


class CliContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        isolation = contextlib.ExitStack()
        cls.addClassCleanup(isolation.close)
        cls.blocked_calls = []
        cls.forbidden_imports = []

        def no_network(*_args, **_kwargs):
            raise AssertionError("隔离测试禁止联网")

        for owner, name in ((socket, "create_connection"), (socket, "getaddrinfo"),
                            (socket, "gethostbyname"), (socket, "gethostbyname_ex"),
                            (socket.socket, "connect"), (socket.socket, "connect_ex"),
                            (socket.socket, "send"), (socket.socket, "sendall"), (socket.socket, "sendto")):
            blocked = isolation.enter_context(mock.patch.object(owner, name, side_effect=no_network))
            cls.blocked_calls.append(blocked)
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_MODULES):
                cls.forbidden_imports.append(name)
                raise AssertionError(f"消费者不能导入准备/API模块: {name}")
            return real_import(name, *args, **kwargs)

        class ImportGuard:
            def find_spec(self, fullname, *_args):
                if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in FORBIDDEN_MODULES):
                    cls.forbidden_imports.append(fullname)
                    raise AssertionError(f"消费者不能加载准备/API模块: {fullname}")
                return None

        isolation.enter_context(mock.patch.object(builtins, "__import__", side_effect=guarded_import))
        isolation.enter_context(mock.patch.object(sys, "meta_path", [ImportGuard(), *sys.meta_path]))
        isolation.enter_context(mock.patch.object(sys, "dont_write_bytecode", True))
        isolation.enter_context(mock.patch.object(sys, "path", list(sys.path)))
        spec = importlib.util.spec_from_file_location("_hdeg_consumer_cli_test", CLI_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载消费 CLI")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)
        cls.adapter = cls.cli._lrc_adapter(HDEG_ROOT)

    def setUp(self):
        super().setUp()
        for blocked in self.blocked_calls:
            blocked.reset_mock()
        self.forbidden_imports.clear()
        temporary = tempfile.TemporaryDirectory(prefix="hdeg-consumer-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.work = self.root / "RJ00000000_中性夹具"
        self.source = self.work / "本編" / "01_朝の案内.ja.lrc"
        self.target = self.source.with_name("01_朝の案内.lrc")
        self.source.parent.mkdir(parents=True)
        self.jp = ["おはようございます", "駅へ行きましょう", "ここで少し休みましょう", "また明日会いましょう"]
        self.cn = ["早上好", "一起去车站吧", "在这里休息一下吧", "明天再见吧"]
        self.source.write_text("[ti:本地中性测试]\n[ar:测试]\n"
                               f"[00:00.25]{self.jp[0]}\n[00:01.00]\n[00:03.75]{self.jp[1]}\n"
                               f"[00:08.20]{self.jp[2]}\n[00:12.00]{self.jp[3]}\n[00:13.50]\n", encoding="utf-8")
        self.track = "本編/01_朝の案内"
        self.package = self.work / ".hdeg-translation"
        self.manifest_path = self.package / "manifest.json"
        self.track_asset = "data/prepared-1/tracks/track-1.json"
        self.runtime = self.root / ".runtime"
        self.scratch = self.root / "inputs"
        self.scratch.mkdir()
        self.serial = 0
        patches = contextlib.ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(mock.patch.multiple(self.cli, RUNTIME_DIR=self.runtime,
                                                   SESSIONS_ROOT=self.runtime / "sessions", LOGS_ROOT=self.runtime / "translate_logs",
                                                   SKILL_CONFIG_PATH=self.root / "skill-config.json"))
        self.skill_cfg = {"app": {"polish_after_translate": False, "srt_vtt_resync": False}, "terms": {"駅": "不可读取配置译名"}}
        self._write_json(self.cli.SKILL_CONFIG_PATH, {"hdeg_root": str(HDEG_ROOT)})
        self.manifest = {"schema_version": 1, "status": "READY", "prepare_id": "prepared-1", "work_root": str(self.work),
                         "rules_version": "consumer-tests-v1", "created_at": "2026-11-19T10:00:00Z",
                         "input_fingerprint": hashlib.sha256(b"fixture inputs").hexdigest(), "contexts": {}, "tracks": [], "assets": {}}
        self.contexts = {}
        for mode in ("original", "normalized"):
            context = {"worldview": {"worldview": f"{mode} 快照：清晨在车站集合"}, "terms": {"駅": "车站"},
                       "alias": ["駅前 → 駅"], "statuses": {"worldview": "ready"}, "sources": ["snapshot-only"]}
            self.contexts[mode] = context
            ref = f"data/prepared-1/context.{mode}.json"
            self.manifest["contexts"][mode] = ref
            self._asset(ref, context)
        self.track_data = self._make_track(self.source, self.track_asset)
        self._save_manifest()
        self.addCleanup(self._assert_isolation)

    def _assert_isolation(self):
        self.assertEqual(self.forbidden_imports, [])
        for blocked in self.blocked_calls:
            blocked.assert_not_called()

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _asset(self, ref, data):
        path = self._write_json(self.package / ref, data)
        self.manifest["assets"][ref] = hashlib.sha256(path.read_bytes()).hexdigest()
        return ref

    def _make_track(self, source, asset):
        key = source.relative_to(self.work).as_posix()[:-len(".ja.lrc")]
        sub = self.adapter.parse_subtitle_file(source)
        indices = [i for i, t in enumerate(sub.original_lyrics) if t.strip()]
        texts = [sub.original_lyrics[i] for i in indices]
        chunks = [list(range(i, min(i + 2, len(texts)))) for i in range(0, len(texts), 2)]
        track = {"track_key": key, "source_ja": source.relative_to(self.work).as_posix(), "target_lrc": key + ".lrc",
                 "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "texts_eff": texts,
                 "time_tags": [sub.time_tags[i] for i in indices], "lyric_indices": indices, "chunks": chunks,
                 "scriptbook_lines": texts, "aligned_eff": {str(i): {"sb": text, "conf": 1.0, "span": [i, i]} for i, text in enumerate(texts)},
                 "polish_after_translate": False, "output": {"srt_vtt_resync": False, "has_video": False},
                 "prompts": {}, "polish_templates": {}}
        asset_id = Path(asset).stem
        for mode in ("original", "normalized"):
            refs = []
            for chunk, positions in enumerate(chunks):
                payload = {"lines": [{"index": i, "text": texts[pos], "sb": texts[pos]} for i, pos in enumerate(positions, 1)]}
                prompt = {"system_prompt": f"{mode} 完整规则 " + json.dumps(self.contexts[mode], ensure_ascii=False),
                          "user_prompt": "完整台本 " + json.dumps(texts, ensure_ascii=False) + "\n<asr>\n" + json.dumps(payload, ensure_ascii=False) + "\n</asr>"}
                refs.append(self._asset(f"data/prepared-1/prompts/{asset_id}.{mode}.{chunk}.json", prompt))
            track["prompts"][mode] = refs
            track["polish_templates"][mode] = self._asset(f"data/prepared-1/prompts/{asset_id}.{mode}.polish.json",
                                                         {"system_prompt": f"{mode} 固定润色规则", "user_prefix": "<draft_data>\n", "user_suffix": "\n</draft_data>"})
        self._asset(asset, track)
        self.manifest["tracks"].append({**{k: track[k] for k in ("track_key", "source_ja", "target_lrc", "source_sha256")}, "asset": asset})
        return track

    def _save_manifest(self):
        self._write_json(self.manifest_path, self.manifest)

    def _save_track(self):
        self._asset(self.track_asset, self.track_data)
        self._save_manifest()

    def _input(self, data):
        self.serial += 1
        return self._write_json(self.scratch / f"input_{self.serial}.json", data)

    def _invoke(self, command, **args):
        return getattr(self.cli, f"cmd_{command}")(argparse.Namespace(**args), self.skill_cfg, {}, HDEG_ROOT)

    def _open(self, **overrides):
        args = {"work": str(self.work), "track": self.track, "context": "original", "force": False}
        args.update(overrides)
        return self._invoke("open_track", **args)

    def _submit_payload(self, session, chunk, data, **overrides):
        args = {"session": session, "chunk": chunk, "in_file": str(self._input(data)), "allow_echo": False,
                "full_next_prompt": False, "skip_polish": False}
        args.update(overrides)
        return self._invoke("submit_chunk", **args)

    def _submit(self, session, chunk, texts, reverse=False, **overrides):
        items = [{"index": i, "text": text} for i, text in enumerate(texts, 1)]
        if reverse:
            items.reverse()
        return self._submit_payload(session, chunk, {"translations": items}, **overrides)

    def _complete(self, session, **overrides):
        self.assertEqual(self._submit(session, 0, self.cn[:2])["status"], "NEXT_CHUNK")
        return self._submit(session, 1, self.cn[2:], **overrides)

    def _polish(self, session, changes):
        return self._invoke("submit_polish", session=session, in_file=str(self._input({"polish": changes})))

    def _fail(self, session):
        return self._invoke("fail_track", session=session, reason="本地测试主动停止", raw_file="")

    def _state(self, session):
        path = self.cli._find_track_state_path(session)
        self.assertTrue(path.resolve().is_relative_to(self.runtime))
        return json.loads(path.read_text(encoding="utf-8"))

    def _prompt(self, result):
        path = Path(result["prompt_file"])
        self.assertTrue(path.resolve().is_relative_to(self.runtime))
        return path.read_text(encoding="utf-8")

    def _asr(self, result):
        return json.loads(self._prompt(result).split("<asr>", 1)[1].split("</asr>", 1)[0])["lines"]

    def _validate(self, data, allow_echo=False):
        return self.cli._validate_and_extract_chunk_translations(json.dumps(data, ensure_ascii=False), self.jp[:2], allow_echo)[0]

    def _package_bytes(self):
        return {p.relative_to(self.package).as_posix(): p.read_bytes() for p in self.package.rglob("*") if p.is_file()}

    def _main(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", [str(CLI_PATH), *arguments]), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                self.cli.main()
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_two_chunks_bind_strict_ids_to_source_timeline_and_leave_package_unchanged(self):
        before = self._package_bytes()
        source_bytes = self.source.read_bytes()
        opened = self._open()
        self.assertEqual(opened["status"], "OPEN")
        self.assertEqual(opened["total_chunks"], 2)
        self.assertEqual(opened["context_mode"], "original")
        self.assertEqual([item["index"] for item in self._asr(opened)], [1, 2])
        self.assertEqual([item["text"] for item in self._asr(opened)], self.jp[:2])
        session = opened["session_id"]
        self.assertEqual(self._state(session)["consumer_schema_version"], 1)
        self.assertEqual(self._state(session)["lyric_indices"], [0, 2, 3, 4])
        following = self._submit(session, 0, self.cn[:2], reverse=True)
        self.assertEqual(following["status"], "NEXT_CHUNK")
        self.assertEqual([item["index"] for item in self._asr(following)], [1, 2])
        self.assertEqual([item["text"] for item in self._asr(following)], self.jp[2:])
        self.assertIn(json.dumps({"lines": self.cn[:2]}, ensure_ascii=False), self._prompt(following))
        committed = self._submit(session, 1, self.cn[2:], reverse=True)
        self.assertEqual(committed["status"], "COMMITTED")
        self.assertEqual(self._state(session)["status"], "COMMITTED")
        output = self.adapter.parse_subtitle_file(self.target)
        self.assertEqual(output.original_lyrics, self.cn)
        self.assertEqual(output.time_tags, self.track_data["time_tags"])
        self.assertIn("[ti:本地中性测试]", self.target.read_text(encoding="utf-8"))
        self.assertFalse(self.target.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(self._package_bytes(), before)
        self.assertTrue(Path(committed["work_log"]).is_relative_to(self.runtime))

    def test_existing_target_skips_without_force_and_creates_no_session(self):
        self.target.write_bytes(b"existing target\n")
        self.assertEqual(self._open()["status"], "SKIPPED_EXISTS")
        self.assertEqual(self.target.read_bytes(), b"existing target\n")
        self.assertFalse(self.runtime.exists())

    def test_force_replaces_only_unchanged_existing_target(self):
        self.target.write_bytes(b"explicit replacement\n")
        session = self._open(force=True)["session_id"]
        self.assertEqual(self._complete(session)["status"], "COMMITTED")
        self.assertEqual(self.adapter.parse_subtitle_file(self.target).original_lyrics, self.cn)

    def test_force_refuses_target_changed_after_open(self):
        self.target.write_bytes(b"before\n")
        session = self._open(force=True)["session_id"]
        self._submit(session, 0, self.cn[:2])
        self.target.write_bytes(b"another writer\n")
        with self.assertRaises(self.cli.CliError):
            self._submit(session, 1, self.cn[2:])
        self.assertEqual(self.target.read_bytes(), b"another writer\n")

    def test_target_created_after_open_is_not_overwritten(self):
        session = self._open()["session_id"]
        self._submit(session, 0, self.cn[:2])
        self.target.write_bytes(b"another writer\n")
        with self.assertRaises(self.cli.CliError):
            self._submit(session, 1, self.cn[2:])
        self.assertEqual(self.target.read_bytes(), b"another writer\n")

    def test_atomic_publish_handles_creation_race_without_overwriting(self):
        session = self._open()["session_id"]
        self._submit(session, 0, self.cn[:2])
        real_link = os.link

        def racing_link(source, target):
            Path(target).write_bytes(b"winner\n")
            return real_link(source, target)

        with mock.patch.object(self.cli.os, "link", side_effect=racing_link), self.assertRaises(self.cli.CliError):
            self._submit(session, 1, self.cn[2:])
        self.assertEqual(self.target.read_bytes(), b"winner\n")
        self.assertFalse(list(self.target.parent.glob(".*.lrc")))

    def test_no_unsafe_fallback_if_atomic_link_is_unavailable(self):
        session = self._open()["session_id"]
        self._submit(session, 0, self.cn[:2])
        with mock.patch.object(self.cli.os, "link", side_effect=OSError("unsupported")), self.assertRaises(self.cli.CliError):
            self._submit(session, 1, self.cn[2:])
        self.assertFalse(self.target.exists())

    def test_source_hash_detects_same_size_and_same_mtime_changes(self):
        session = self._open()["session_id"]
        self._submit(session, 0, self.cn[:2])
        stat = self.source.stat()
        self.source.write_bytes(self.source.read_bytes().replace("駅へ".encode(), "町へ".encode()))
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, stat.st_size)
        with self.assertRaises(self.cli.CliError):
            self._submit(session, 1, self.cn[2:])
        self.assertFalse(self.target.exists())

    def test_source_changed_during_staging_is_not_published(self):
        session = self._open()["session_id"]
        self._submit(session, 0, self.cn[:2])
        writer = self.adapter.write_subtitle_file_nonempty

        def mutate_source(*args):
            writer(*args)
            self.source.write_bytes(self.source.read_bytes() + b"\n")

        with mock.patch.object(self.adapter, "write_subtitle_file_nonempty", side_effect=mutate_source), self.assertRaises(self.cli.CliError):
            self._submit(session, 1, self.cn[2:])
        self.assertFalse(self.target.exists())

    def test_both_contexts_and_prompts_are_frozen_at_open(self):
        for mode in ("original", "normalized"):
            with self.subTest(mode=mode):
                opened = self._open(context=mode)
                session = opened["session_id"]
                expected = json.loads(json.dumps(self.contexts[mode]))
                self.assertEqual(self._state(session)["context_snapshot"], expected)
                self.assertIn(expected["worldview"]["worldview"], self._prompt(opened))
                # 完全破坏包后，后续块仍只使用冻结快照。
                saved = self.manifest_path.read_bytes()
                self.manifest_path.write_bytes(b"broken manifest")
                ref = self.track_data["prompts"][mode][1]
                prompt_bytes = (self.package / ref).read_bytes()
                (self.package / ref).write_bytes(b"changed asset")
                following = self._submit(session, 0, self.cn[:2], full_next_prompt=True)
                self.assertIn(expected["worldview"]["worldview"], self._prompt(following))
                self.assertNotIn("changed asset", self._prompt(following))
                self.assertEqual(self._state(session)["context_snapshot"], expected)
                self._fail(session)
                self.manifest_path.write_bytes(saved)
                (self.package / ref).write_bytes(prompt_bytes)

    def test_polish_template_and_plan_are_frozen(self):
        self.track_data["polish_after_translate"] = True
        self._save_track()
        session = self._open()["session_id"]
        self.track_data["polish_after_translate"] = False
        self._save_track()
        ref = self.track_data["polish_templates"]["original"]
        self._asset(ref, {"system_prompt": "NEW", "user_prefix": "NEW", "user_suffix": "NEW"})
        self._save_manifest()
        draft = self._complete(session)
        self.assertEqual(draft["status"], "NEED_POLISH")
        prompt = self._prompt(draft)
        payload = json.loads(prompt.split("<draft_data>\n", 1)[1].split("\n</draft_data>", 1)[0])
        self.assertEqual(payload, {"lines": [{"index": i, "text": t} for i, t in enumerate(self.cn, 1)]})
        self.assertIn("original 固定润色规则", prompt)
        self.assertNotIn("NEW", prompt)
        self.assertEqual(self._polish(session, {"2": "一起往车站走吧"})["status"], "COMMITTED")
        self.assertEqual(self.adapter.parse_subtitle_file(self.target).original_lyrics, [self.cn[0], "一起往车站走吧", *self.cn[2:]])

    def test_skill_config_cannot_toggle_fixed_polish(self):
        self.skill_cfg["app"]["polish_after_translate"] = True
        session = self._open()["session_id"]
        self.assertEqual(self._complete(session)["status"], "COMMITTED")

    def test_explicit_skip_polish_compatibility_is_preserved(self):
        self.track_data["polish_after_translate"] = True
        self._save_track()
        session = self._open()["session_id"]
        self.assertEqual(self._complete(session, skip_polish=True)["status"], "COMMITTED")

    def test_sparse_empty_polish_keeps_draft(self):
        self.track_data["polish_after_translate"] = True
        self._save_track()
        session = self._open()["session_id"]
        self.assertEqual(self._complete(session)["status"], "NEED_POLISH")
        result = self._polish(session, {})
        self.assertEqual(result["polished_lines"], 0)
        self.assertEqual(self.adapter.parse_subtitle_file(self.target).original_lyrics, self.cn)

    def test_invalid_sparse_polish_does_not_commit(self):
        self.track_data["polish_after_translate"] = True
        self._save_track()
        session = self._open()["session_id"]
        self._complete(session)
        for changes in ({"0": "零"}, {"01": "错号"}, {"5": "越界"}, {"1": 3}, {"1": ""}, {"1": "[EMPTY_LINE]"}, {"1": "甲\n乙"}, {"1": "[00:01.00]注入"}):
            with self.subTest(changes=changes), self.assertRaises(self.cli.CliError):
                self._polish(session, changes)
        self.assertEqual(self._state(session)["status"], "NEED_POLISH")
        self.assertFalse(self.target.exists())

    def test_polish_checks_source_hash_again(self):
        self.track_data["polish_after_translate"] = True
        self._save_track()
        session = self._open()["session_id"]
        self._complete(session)
        self.source.write_bytes(b"changed")
        with self.assertRaises(self.cli.CliError):
            self._polish(session, {})
        self.assertFalse(self.target.exists())

    def test_missing_id_does_not_advance(self):
        session = self._open()["session_id"]
        result = self._submit_payload(session, 0, {"translations": [{"index": 1, "text": self.cn[0]}]})
        self.assertEqual(result["status"], "MISSING_INDICES")
        self.assertEqual(result["missing_indices"], [2])
        self.assertEqual(self._state(session)["translated_chunks"], {})
        self.assertEqual(self._submit(session, 0, self.cn[:2])["status"], "NEXT_CHUNK")

    def test_duplicate_out_of_range_and_non_integer_ids_are_rejected(self):
        for index in (0, -1, 3, "1", True, 1.0, None):
            with self.subTest(index=index):
                self.assertNotEqual(self._validate({"translations": [{"index": index, "text": "早"}, {"index": 2, "text": "车站"}]}), "OK")
        self.assertEqual(self._validate({"translations": [{"index": 1, "text": "甲"}, {"index": 1, "text": "乙"}]}), "DUPLICATE_INDICES")

    def test_translation_types_empty_values_and_unindexed_arrays_are_rejected(self):
        for text in (7, None, True, [], {}, "", "   ", "[EMPTY_LINE]"):
            with self.subTest(text=text):
                self.assertNotEqual(self._validate({"translations": [{"index": 1, "text": text}, {"index": 2, "text": "车站"}]}), "OK")
        for payload in (self.cn[:2], {"translations": self.cn[:2]}, {"translations": []}):
            self.assertNotEqual(self._validate(payload), "OK")

    def test_newline_control_and_timestamp_injection_are_rejected(self):
        for text in ("甲\n乙", "甲\r乙", "甲\x00乙", "甲\u2028乙", "[00:01.00]乙", "甲[1:02]", "00:00:01,000 --> 00:00:02,000"):
            with self.subTest(text=text):
                self.assertEqual(self._validate({"translations": [{"index": 1, "text": text}, {"index": 2, "text": "车站"}]}), "UNSAFE_TRANSLATION")

    def test_echo_detection_uses_local_punctuation_regex_and_explicit_override(self):
        payload = {"translations": [{"index": 1, "text": "「おはようございます」！"}, {"index": 2, "text": "车站"}]}
        self.assertEqual(self._validate(payload), "UNTRANSLATED_ECHO")
        self.assertEqual(self._validate(payload, allow_echo=True), "OK")

    def test_duplicate_json_object_keys_are_rejected(self):
        raw = '{"translations":[{"index":1,"index":2,"text":"甲"},{"index":2,"text":"乙"}]}'
        self.assertEqual(self.cli._validate_and_extract_chunk_translations(raw, self.jp[:2], False)[0], "INVALID_JSON")

    def test_already_accepted_chunk_and_out_of_order_chunk_are_rejected(self):
        session = self._open()["session_id"]
        with self.assertRaises(self.cli.CliError):
            self._submit(session, 1, self.cn[2:])
        self._submit(session, 0, self.cn[:2])
        with self.assertRaises(self.cli.CliError):
            self._submit(session, 0, self.cn[:2])

    def test_commit_is_terminal_for_all_state_commands(self):
        session = self._open()["session_id"]
        self._complete(session)
        before = self.target.read_bytes()
        for action in (lambda: self._submit(session, 0, self.cn[:2]), lambda: self._polish(session, {}), lambda: self._fail(session)):
            with self.assertRaises(self.cli.CliError):
                action()
        self.assertEqual(self.target.read_bytes(), before)

    def test_fail_is_terminal_and_logs_only_in_runtime(self):
        before = self._package_bytes()
        session = self._open()["session_id"]
        stopped = self._fail(session)
        self.assertEqual(stopped["status"], "FAILED_STOPPED")
        self.assertNotIn("next_action", stopped)
        self.assertTrue(Path(stopped["failure_log"]).is_relative_to(self.runtime))
        self.assertFalse((self.target.parent / "log").exists())
        self.assertEqual(self._package_bytes(), before)
        for action in (lambda: self._submit(session, 0, self.cn[:2]), lambda: self._polish(session, {}), lambda: self._fail(session)):
            with self.assertRaises(self.cli.CliError):
                action()
        self.assertFalse(self.target.exists())

    def test_fail_from_need_polish_is_terminal(self):
        self.track_data["polish_after_translate"] = True
        self._save_track()
        session = self._open()["session_id"]
        self._complete(session)
        self.assertEqual(self._fail(session)["status"], "FAILED_STOPPED")
        with self.assertRaises(self.cli.CliError):
            self._polish(session, {})

    def test_fail_can_stop_after_source_changes_and_records_check(self):
        session = self._open()["session_id"]
        self.source.write_bytes(b"changed")
        self.assertEqual(self._fail(session)["status"], "FAILED_STOPPED")
        self.assertFalse(self._state(session)["source_unchanged_at_stop"])

    def test_polish_before_draft_is_rejected(self):
        session = self._open()["session_id"]
        with self.assertRaises(self.cli.CliError):
            self._polish(session, {})

    def test_illegal_session_ids_are_rejected(self):
        session = self._open()["session_id"]
        for invalid in ("", "*", "?", "[abc]", "../escape", "..\\escape", "/outside/session", "C:/outside/session", f"{session}/../{session}", "missing"):
            for action in (lambda: self._submit(invalid, 0, self.cn[:2]), lambda: self._polish(invalid, {}), lambda: self._fail(invalid)):
                with self.subTest(session=invalid), self.assertRaises(self.cli.CliError):
                    action()

    def test_legacy_sessions_missing_consumer_schema_are_explicitly_rejected(self):
        session = self._open()["session_id"]
        state = self._state(session)
        del state["consumer_schema_version"]
        self._write_json(self.cli._find_track_state_path(session), state)
        for action in (lambda: self._submit(session, 0, self.cn[:2]), lambda: self._polish(session, {}), lambda: self._fail(session)):
            with self.assertRaisesRegex(self.cli.CliError, "consumer schema"):
                action()

    def test_session_and_target_locks_are_acquired(self):
        session = self._open()["session_id"]
        paths = []
        actual = self.cli._file_lock

        @contextlib.contextmanager
        def observed(path):
            paths.append(path)
            with actual(path):
                yield

        with mock.patch.object(self.cli, "_file_lock", side_effect=observed):
            self._complete(session)
        self.assertEqual(sum(p.name == "state.lock" for p in paths), 2)
        self.assertEqual(sum(p.parent.name == "target_locks" for p in paths), 1)
        self.assertTrue(all(p.is_relative_to(self.runtime) for p in paths))

    def test_missing_package_never_falls_back_to_legacy_caches(self):
        self.manifest_path.unlink()
        self._write_json(self.work / ".worldview.json", {"worldview": "不能读取"})
        self._write_json(self.work / "_scriptbook_clean.json", {self.track: self.jp})
        with self.assertRaisesRegex(self.cli.CliError, "缺少准备包"):
            self._open()
        self.assertFalse(self.runtime.exists())

    def test_wrong_schema_nonready_and_wrong_work_root_fail_fast(self):
        for key, value in (("schema_version", 2), ("schema_version", True), ("status", "PREPARING"), ("status", "FAILED"),
                           ("work_root", str(self.root)), ("work_root", "relative")):
            old = self.manifest[key]
            with self.subTest(key=key, value=value):
                self.manifest[key] = value
                self._save_manifest()
                with self.assertRaises(self.cli.CliError):
                    self._open()
            self.manifest[key] = old
        self.assertFalse(self.runtime.exists())

    def test_missing_asset_and_unlisted_reference_fail_fast(self):
        ref = self.track_data["prompts"]["original"][0]
        path = self.package / ref
        saved = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(self.cli.CliError, "缺失准备资产"):
            self._open()
        path.write_bytes(saved)
        del self.manifest["assets"][ref]
        self._save_manifest()
        with self.assertRaisesRegex(self.cli.CliError, "未登记资产"):
            self._open()

    def test_all_asset_hashes_are_checked_including_unused_mode(self):
        ref = self.track_data["prompts"]["normalized"][1]
        (self.package / ref).write_bytes(b"tampered")
        with self.assertRaisesRegex(self.cli.CliError, "摘要不匹配"):
            self._open(context="original")
        self.assertFalse(self.runtime.exists())

    def test_invalid_hash_and_source_change_prevent_open_even_with_existing_target(self):
        self.manifest["assets"][self.track_asset] = "invalid"
        self._save_manifest()
        with self.assertRaisesRegex(self.cli.CliError, "SHA256"):
            self._open()
        self._save_track()
        self.source.write_bytes(self.source.read_bytes() + b"\n")
        self.target.write_bytes(b"existing")
        with self.assertRaisesRegex(self.cli.CliError, "源字幕摘要"):
            self._open()
        self.assertEqual(self.target.read_bytes(), b"existing")

    def test_asset_and_context_semantics_are_validated(self):
        original = json.loads(json.dumps(self.track_data))
        for key, value in (("texts_eff", ["different"]), ("time_tags", ["[00:00.00]"] * 4),
                           ("lyric_indices", [2, 3, 4, 5]), ("chunks", [[0, 1], [1, 3]]), ("chunks", [[True, 1], [2, 3]]),
                           ("polish_after_translate", "false"), ("output", {"srt_vtt_resync": 1, "has_video": False})):
            with self.subTest(key=key), self.assertRaises(self.cli.CliError):
                self.track_data = {**original, key: value}
                self._save_track()
                self._open()
        self.track_data = original
        self._save_track()
        ref = self.manifest["contexts"]["original"]
        self._asset(ref, {"worldview": [], "terms": {}, "alias": []})
        self._save_manifest()
        with self.assertRaisesRegex(self.cli.CliError, "context"):
            self._open()
        self.assertFalse(self.runtime.exists())

    def test_prompt_json_and_polish_template_shape_are_validated(self):
        ref = self.track_data["prompts"]["original"][0]
        original = (self.package / ref).read_bytes()
        for prompt in ({"system_prompt": "系统"}, {"system_prompt": "", "user_prompt": "用户"}, {"system_prompt": "系统", "user_prompt": 7}):
            self._asset(ref, prompt)
            self._save_manifest()
            with self.assertRaises(self.cli.CliError):
                self._open()
        (self.package / ref).write_bytes(original)
        self.manifest["assets"][ref] = hashlib.sha256(original).hexdigest()
        self._asset(self.track_data["polish_templates"]["normalized"], {"system_prompt": "系统", "user_prefix": "前缀"})
        self._save_manifest()
        with self.assertRaisesRegex(self.cli.CliError, "polish"):
            self._open()

    def test_package_paths_reject_traversal_absolute_windows_and_unlisted_references(self):
        for unsafe in ("../outside.json", "..\\outside.json", "/outside.json", "C:/outside.json", "data/../context.json", "https://example.invalid/x"):
            with self.subTest(path=unsafe), self.assertRaises(self.cli.CliError):
                self.manifest["contexts"]["original"] = unsafe
                self._save_manifest()
                self._open()
        self.assertFalse(self.runtime.exists())

    def test_track_source_target_and_key_must_match_manifest_and_work(self):
        entry = self.manifest["tracks"][0]
        for key, value in (("source_ja", "../outside.ja.lrc"), ("target_lrc", "../outside.lrc"),
                           ("target_lrc", ".hdeg-translation/data/out.lrc"), ("track_key", "wrong-key")):
            old = entry[key]
            with self.subTest(key=key), self.assertRaises(self.cli.CliError):
                entry[key] = value
                self._save_manifest()
                self._open()
            entry[key] = old
        self._save_manifest()
        self.track_data["source_sha256"] = "0" * 64
        self._save_track()
        with self.assertRaisesRegex(self.cli.CliError, "与 manifest 不一致"):
            self._open()

    def test_duplicate_tracks_and_incomplete_prompt_plan_are_rejected(self):
        self.manifest["tracks"].append(dict(self.manifest["tracks"][0]))
        self._save_manifest()
        with self.assertRaisesRegex(self.cli.CliError, "重复"):
            self._open()
        self.manifest["tracks"].pop()
        self.track_data["prompts"]["original"] = self.track_data["prompts"]["original"][:1]
        self._save_track()
        with self.assertRaisesRegex(self.cli.CliError, "分块"):
            self._open()

    def test_package_changes_during_open_are_rejected(self):
        actual = self.adapter.parse_subtitle_file

        def change_manifest(path):
            sub = actual(path)
            self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b" ")
            return sub

        with mock.patch.object(self.adapter, "parse_subtitle_file", side_effect=change_manifest), self.assertRaisesRegex(self.cli.CliError, "manifest 发生变化"):
            self._open()
        self.assertFalse(self.runtime.exists())

    def test_track_argument_cannot_escape_work_and_unknown_track_has_no_fallback(self):
        for request in (str(self.root / "outside.ja.lrc"), "../outside.ja.lrc", "..\\outside.ja.lrc", "unknown"):
            with self.subTest(request=request), self.assertRaises(self.cli.CliError):
                self._open(track=request)
        self.assertFalse(self.runtime.exists())
        self.assertEqual(self._open(track=str(self.source))["status"], "OPEN")

    def test_ambiguous_stem_is_rejected_but_relative_key_selects_exact_track(self):
        duplicate = self.work / "特典" / self.source.name
        duplicate.parent.mkdir()
        duplicate.write_bytes(self.source.read_bytes())
        self._make_track(duplicate, "data/prepared-1/tracks/track-2.json")
        self._save_manifest()
        with self.assertRaisesRegex(self.cli.CliError, "歧义"):
            self._open(track="01_朝の案内")
        self.assertEqual(self._open()["track_key"], self.track)

    def test_scope_scan_supports_work_parent_and_child_without_sibling_tracks(self):
        sibling = self.work / "特典" / self.source.name
        sibling.parent.mkdir()
        sibling.write_bytes(self.source.read_bytes())
        self._make_track(sibling, "data/prepared-1/tracks/track-2.json")
        self._save_manifest()
        before = self._package_bytes()
        for scope, count in ((self.work, 2), (self.root, 2), (self.source.parent, 1)):
            with self.subTest(scope=scope):
                scan = self._invoke("scan", path=str(scope))
                self.assertEqual(scan["status"], "READY")
                self.assertEqual(scan["total_pending_tracks"], count)
                if count == 1:
                    self.assertEqual(scan["works"][0]["pending_tracks"][0]["track_key"], self.track)
        self.assertEqual(self._package_bytes(), before)
        self.assertFalse(self.runtime.exists())

    def test_selected_package_wide_scan_reports_unprepared_tracks_after_prepared_track_finishes(self):
        # 模拟只准备 本編 子目录：作品包为 READY，但不覆盖 other 下尚未翻译的音轨。
        other = self.work / "other" / "02_追加.ja.lrc"
        other.parent.mkdir()
        other.write_bytes(self.source.read_bytes())
        self.target.write_bytes(b"prepared track already finished\n")
        before = self._package_bytes()
        selected = self._invoke("scan", path=str(self.source.parent))
        self.assertEqual(selected["status"], "READY")
        self.assertEqual(selected["total_pending_tracks"], 0)
        for scope in (self.work, self.root, other.parent):
            with self.subTest(scope=scope):
                scanned = self._invoke("scan", path=str(scope))
                self.assertEqual(scanned["status"], "UNPREPARED")
                self.assertEqual(scanned["unprepared_works"], 1)
                self.assertEqual(scanned["pending_works"], 1)
                self.assertEqual(scanned["total_pending_tracks"], 1)
                work = scanned["works"][0]
                self.assertEqual(work["preparation_status"], "UNPREPARED")
                self.assertEqual(work["prepare_id"], self.manifest["prepare_id"])
                pending = work["pending_tracks"][0]
                self.assertEqual(pending["track_key"], "other/02_追加")
                self.assertEqual(pending["source_ja"], str(other))
                self.assertEqual(pending["target_lrc"], str(other.with_name("02_追加.lrc")))
                self.assertEqual(pending["preparation_status"], "UNPREPARED")
        self.assertEqual(self._package_bytes(), before)
        self.assertFalse(self.runtime.exists())

    def test_selected_package_scan_keeps_ready_pending_tracks_and_skips_completed_unlisted_tracks(self):
        other = self.work / "other" / "02_追加.ja.lrc"
        other.parent.mkdir()
        other.write_bytes(self.source.read_bytes())
        scanned = self._invoke("scan", path=str(self.work))
        self.assertEqual(scanned["status"], "UNPREPARED")
        self.assertEqual(scanned["total_pending_tracks"], 2)
        pending = {track["track_key"]: track for track in scanned["works"][0]["pending_tracks"]}
        self.assertEqual(pending[self.track]["preparation_status"], "READY")
        self.assertEqual(pending["other/02_追加"]["preparation_status"], "UNPREPARED")
        other.with_name("02_追加.lrc").write_bytes(b"already finished\n")
        complete_other = self._invoke("scan", path=str(self.work))
        self.assertEqual(complete_other["status"], "READY")
        self.assertEqual(complete_other["total_pending_tracks"], 1)
        self.assertEqual(complete_other["works"][0]["pending_tracks"][0]["track_key"], self.track)

    def test_scan_does_not_leave_scope_to_find_unrelated_package(self):
        outside = self.root / "RJ99999999_unrelated"
        outside.mkdir()
        (outside / ".hdeg-translation").mkdir()
        (outside / ".hdeg-translation" / "manifest.json").write_bytes(b"broken unrelated")
        scan = self._invoke("scan", path=str(self.source.parent))
        self.assertEqual(scan["status"], "READY")
        self.assertEqual(scan["total_works"], 1)
        self.assertEqual(scan["works"][0]["work_dir"], str(self.work))

    def test_scan_reports_ready_failed_and_unprepared_explicitly(self):
        self.target.write_bytes(b"finished")
        ready = self._invoke("scan", path=str(self.work))
        self.assertEqual(ready["status"], "READY")
        self.assertEqual(ready["total_pending_tracks"], 0)
        self.assertEqual(ready["works"][0]["skipped_existing_lrc"], 1)
        self.manifest_path.write_bytes(b"broken")
        failed = self._invoke("scan", path=str(self.work))
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["works"][0]["preparation_status"], "FAILED")
        self.manifest_path.unlink()
        unprepared = self._invoke("scan", path=str(self.work))
        self.assertEqual(unprepared["status"], "UNPREPARED")
        self.assertEqual(unprepared["works"][0]["preparation_status"], "UNPREPARED")
        self.assertFalse(self.runtime.exists())

    def test_scan_reports_source_changes_as_failed(self):
        self.source.write_bytes(b"changed")
        scanned = self._invoke("scan", path=str(self.work))
        self.assertEqual(scanned["status"], "FAILED")
        self.assertEqual(scanned["total_pending_tracks"], 0)

    def test_output_plan_from_track_syncs_existing_subtitles_and_ignores_skill_config(self):
        self.track_data["output"] = {"srt_vtt_resync": True, "has_video": True}
        self._save_track()
        srt = self.target.with_suffix(".srt")
        vtt = self.target.with_suffix(".vtt")
        self.adapter.write_srt_from_lrc(self.source, srt)
        # 源含空歌词行；同步夹具应与有效译文行数一致。
        srt.write_text("".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i+1:02d},000\n原文{i}\n\n" for i in range(1, 5)), encoding="utf-8")
        timestamps = [line for line in srt.read_text(encoding="utf-8").splitlines() if "-->" in line]
        session = self._open()["session_id"]
        self.track_data["output"] = {"srt_vtt_resync": False, "has_video": False}
        self._save_track()
        result = self._complete(session)
        self.assertEqual(result["synced_srt_vtt"], 1)
        self.assertEqual(result["generated_video_subs"], [vtt.name])
        self.assertEqual(self.adapter.parse_subtitle_file(srt).original_lyrics, self.cn)
        self.assertEqual(self.adapter.parse_subtitle_file(vtt).original_lyrics, self.cn)
        self.assertEqual([line for line in srt.read_text(encoding="utf-8").splitlines() if "-->" in line], timestamps)

    def test_disabled_output_plan_does_not_generate_despite_video_or_skill_config(self):
        self.target.with_suffix(".mp4").write_bytes(b"video placeholder")
        self.skill_cfg["app"]["srt_vtt_resync"] = True
        session = self._open()["session_id"]
        self._complete(session)
        self.assertFalse(self.target.with_suffix(".srt").exists())
        self.assertFalse(self.target.with_suffix(".vtt").exists())

    def test_output_warning_does_not_reopen_committed_session(self):
        self.track_data["output"] = {"srt_vtt_resync": True, "has_video": True}
        self._save_track()
        session = self._open()["session_id"]
        self._submit(session, 0, self.cn[:2])
        with mock.patch.object(self.adapter, "write_srt_from_lrc", side_effect=OSError("fixture failure")):
            result = self._submit(session, 1, self.cn[2:])
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(self._state(session)["status"], "COMMITTED")
        self.assertTrue(result["warnings"])

    def test_parser_retains_only_consumer_commands_and_fixed_options(self):
        parser = self.cli._build_parser()
        args = parser.parse_args(["open-track", "--work", str(self.work), "--track", self.track])
        self.assertEqual(args.context, "original")
        for suffix in (["--context", "harmonized"], ["--chunk-size", "2"], ["--no-scriptbook"], ["--level", "1"]):
            with self.subTest(suffix=suffix), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(["open-track", "--work", str(self.work), "--track", self.track, *suffix])
        for command in ("prepare-scriptbook", "apply-scriptbook-id", "apply-scriptbook-split", "ensure-worldview", "save-worldview", "ensure-harmonized", "save-harmonized"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args([command, "--work", str(self.work)])
        submit = parser.parse_args(["submit-chunk", "--session", "s", "--chunk", "0", "--in", "f", "--full-next-prompt"])
        self.assertTrue(submit.full_next_prompt)
        self.assertIn("兼容参数", parser._subparsers._group_actions[0].choices["submit-chunk"].format_help())

    def test_main_runs_entire_consumer_path_without_preparation_imports_or_global_config(self):
        before = self._package_bytes()
        env_before = {key: os.environ.get(key) for key in ("UNIDIC_DIR", "MECABRC", "UNIDIC_DICDIR")}
        code, opened, _errors = self._main(["open-track", "--work", str(self.work), "--track", self.track])
        self.assertEqual(code, 0)
        self.assertEqual(opened["status"], "OPEN")
        session = opened["session_id"]
        for chunk, texts in enumerate((self.cn[:2], self.cn[2:])):
            input_file = self._input({"translations": [{"index": i, "text": t} for i, t in enumerate(texts, 1)]})
            code, result, _errors = self._main(["submit-chunk", "--session", session, "--chunk", str(chunk), "--in", str(input_file)])
            self.assertEqual(code, 0)
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(self._package_bytes(), before)
        self.assertEqual({key: os.environ.get(key) for key in env_before}, env_before)
        self.assertFalse(any(name == prefix or name.startswith(prefix + ".") for name in sys.modules for prefix in FORBIDDEN_MODULES))

    def test_main_reports_missing_package_as_error(self):
        self.manifest_path.unlink()
        code, result, _errors = self._main(["open-track", "--work", str(self.work), "--track", self.track])
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("缺少准备包", result["error"])
        self.assertFalse(self.runtime.exists())


if __name__ == "__main__":
    unittest.main()

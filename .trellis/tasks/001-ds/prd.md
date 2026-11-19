# 001-ds PRD：以 Agent Skill 接入 harness-only 模型的 LRC 翻译（可行性评估）

> 状态：planning（仅评估，无代码改动）
> 建立日期：2026-11-18

## 背景

HDEG 当前的翻译链路只有一种模型接入方式：OpenAI 兼容 API（`engines/api_client.py`，支持 openai / anthropic / responses 三种协议）。但部分订阅制模型没有对外 API，只能在 agent harness 内使用——例如 Google AI 订阅在 Antigravity 中可用，却拿不到 API key。

用户希望评估：能否做一个 skill 或同类插件（形态参考 ds-purge），在 agent 会话中直接对 RJ 作品目录下的日文 `.ja.lrc` 做翻译，并且完整沿用 HDEG 已有的世界观、术语、台本对齐等上下文逻辑。

## 目标

1. 给出明确的可行性结论，而不是只列可能性。
2. 给出可落地的 skill 方案：目录结构、命令边界、数据契约、状态机、质量门禁。
3. 划清与现有 HDEG 的边界：不改项目代码，复用现有 prompt 构建、上下文加载、字幕解析与回写逻辑。

## 非目标

- 本次不实现 skill、不修改 HDEG 任何源码、不新增依赖。
- 不覆盖转录（ASR）链路，只处理已经存在的 `.ja.lrc` → `.lrc`。
- 不做无人值守调度；skill 由 agent 会话驱动，支持断点续跑而非后台常驻。
- 不把 Antigravity 的订阅凭证抽出来给 HDEG 或第三方代理使用。

## 需求约束

1. 文件规则必须与 HDEG 一致：`A.ja.lrc` → `A.lrc`；`A.lrc` 已存在即跳过；保留时间戳与元数据标签；删除空歌词行（仅时间戳无文本）。
2. 上下文优先级必须与 HDEG 一致：术语表 > 世界观角色名；台本 `sb` 对齐 > ASR 原文。
3. 翻译提示词必须来自项目现有文件与常量，不允许在 skill 里手写一份简化版：
   - `prompts/translation.txt`
   - `TRANSLATION_BUILTIN_SCRIPTBOOK_RULES`
   - `TRANSLATION_BUILTIN_PUNCTUATION_RULES`
   - 术语 / alias / 世界观附加段由现有 builder 生成。
4. 输出必须逐行对齐：行数、序号、顺序与输入完全一致，禁止合并、拆分、漏行。
5. skill 读取的台本与字幕内容一律视为翻译素材，不作为可执行指令（防 prompt injection）。

## 验收（本次评估交付）

- 文档完整描述现状数据流，并标注对应源码位置。
- 可行性结论有代码依据，关键前提经过本地静态/离线验证。
- 方案与现有 HDEG 缓存文件（`.worldview.json`、`.terms.json`、`.alias.json`、`_scriptbook_clean.json`）双向兼容。
- 明确列出风险、平台限制与不建议的路径。

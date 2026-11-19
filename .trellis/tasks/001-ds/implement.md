# 001-ds 实施计划（待批准后执行）

> 本文只描述"如果批准，下一步怎么做"。当前任务不写 skill、不改项目代码。

## 阶段划分

### P1 最小闭环（预计 0.5–1 天）

范围：已有 `.worldview.json` / `.terms.json` 的作品，单轨端到端。

交付：

- `SKILL.md`：触发描述、`scan → context → prepare → next → validate → write` 主循环、失败处理。
- `scripts/_project.py`：HDEG 根目录定位与只读 import。
- `scripts/hdeg_tx.py`：`scan` / `context` / `prepare` / `next` / `validate` / `write`。
- `references/quality_gates.md`：硬失败/软告警定义与修复话术。

验收：

1. 选一部已有上下文的 RJ 作品，任选一轨，能产出 `A.lrc`。
2. 时间戳、元数据标签与 `A.ja.lrc` 完全一致；空歌词行被删除。
3. `A.lrc` 行数 = 源非空行数；`validate` 零硬失败。
4. `system_prompt.md` 与 `OpenAICompatEngine.build_system_prompt` 输出逐字节一致。
5. 中断后再调用 `next`，只处理未完成的块。

### P2 上下文自举与降级（预计 1 天）

范围：没有 `.worldview.json` 的作品；成人向内容被拒时的四级降级。

交付：

- `bootstrap-worldview`：抽样 → 项目原始分析 prompt → agent 输出 JSON → `--apply` 校验并保存 `.worldview.json`。
- `context` 注入角色名/`special_terms` 到 `.terms.json`（复用 `_inject_worldview_terms`）。
- L2 和谐版世界观生成与 `validate` 的拒绝检测联动。
- `repair` 子命令与 `apply-fix`。

验收：

1. 无缓存作品能生成符合 schema 的 `.worldview.json`，HDEG 可直接加载。
2. 模拟拒答（输出拒绝话术）时能自动降到 L2，最终完成或明确标记失败。
3. 修复只重发失败行，不重发整块。

### P3 批量化与台本（预计 1 天）

范围：整个作品多轨、台本缓存、报告。

交付：

- 多轨循环与 `report`。
- 复用 `_scriptbook_clean.json` 与 `align_asr_scriptbook`；无缓存时给出"跳过台本"的明确提示。
- `prompt_sha256`、manifest 迁移与损坏恢复。

验收：

1. 一部 10 轨以上作品可跨 ≥2 个会话完成，无重复翻译、无漏轨。
2. 有台本缓存的轨道，`0001.in.md` 中出现 `<scriptbook>` 与每行 `sb`。
3. `report` 能准确列出完成、跳过、失败轨及原因。

### P4 可选：HDEG 侧 harness engine（不推荐现在做）

仅当出现以下条件之一再评估：

- 拿到正式 API key 或企业端点；
- 使用的 harness 条款明确允许被外部程序驱动；
- 需要无人值守批量跑。

## 未来文件清单（本次不创建）

```
HDEG/.agents/skills/hdeg-lrc-translate/SKILL.md
HDEG/.agents/skills/hdeg-lrc-translate/scripts/hdeg_tx.py
HDEG/.agents/skills/hdeg-lrc-translate/scripts/_project.py
HDEG/.agents/skills/hdeg-lrc-translate/references/quality_gates.md
HDEG/.agents/skills/hdeg-lrc-translate/references/context_bootstrap.md
```

安装（复制或目录链接）：

- `~/.gemini/config/skills/hdeg-lrc-translate/`（Antigravity）
- `~/.agents/skills/hdeg-lrc-translate/`（ZCode）
- `~/.claude/skills/hdeg-lrc-translate/`（Claude Code）

## 待确认项

1. 主用 harness 是 Antigravity、ZCode 还是 Claude Code？决定安装路径与单块行数（Antigravity 建议 60，弱模型建议 40）。
2. 是否接受在作品根下写 `.hdeg_tx/`？备选是 `%LOCALAPPDATA%\hdeg_tx\<hash>\`，但跨机器不可迁移。
3. Phase 1 是否必须支持 PDF 台本？建议先只吃 `_scriptbook_clean.json`，PDF 放 P3。
4. 是否允许 skill 调用 harness 模型生成 `.worldview.json`？建议允许，否则无缓存作品只能裸翻。

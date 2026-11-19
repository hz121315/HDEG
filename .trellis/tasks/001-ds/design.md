# 001-ds 设计：Agent Skill 形式的外部模型翻译方案

## 1. 现状链路拆解

### 1.1 入口与任务收集

`run_pipeline` / 流式路径按作品组扫描目录，翻译任务由 `_collect_translation_pairs`（`pipeline/orchestrator.py:984`）生成：

- `A.lrc` 已存在 → 跳过（计入 skipped）
- 无 `A.lrc`、有 `A.ja.lrc` → 翻译任务，源为 `A.ja.lrc`，目标为 `A.lrc`
- 无任何字幕、有音频 → 进入转录链路
- 文件名以「汉化/漢化」结尾且开关开启 → 跳过

RJ 作品根由 `find_rj_work_root`（`io_adapter/file_scanner.py:52`）向上查 RJ 号确定，用于归组与作品级上下文。

### 1.2 作品级上下文

每个作品组在翻译前准备三类上下文：

| 上下文 | 来源 | 生成/缓存 |
| --- | --- | --- |
| 世界观 | `worldview.json` 等源文件，或抽样 `.ja.lrc` 后 LLM 分析 | `.worldview.json`；降级用 `和谐版.worldview.json` |
| 术语 | `.terms.json` + 世界观角色名/`special_terms` 注入 | `_inject_worldview_terms`（`pipeline/orchestrator.py:1973`） |
| 台本 | `.txt/.pdf` 识别、分割、清洗 | `_scriptbook_clean.json` + `_split_tracks/`（`pipeline/orchestrator.py:1334`、`:1459`） |

世界观分析与术语注入入口是 `_analyze_work_terms`（`pipeline/orchestrator.py:2019`）。世界观 prompt 与分析 JSON schema 在 `engines/worldview_engine.py:411`、`:472`。

### 1.3 单轨翻译

`translate_one_lrc`（`pipeline/orchestrator.py:2524`）的执行顺序：

1. `parse_subtitle_file` 解析源字幕，保留时间戳结构（`io_adapter/lrc_handler.py:238`）
2. 过滤空行，记录原始索引映射
3. 若有台本，`align_asr_scriptbook` 做 ASR↔台本多对多对齐，得到每行 `sb` 与置信度
4. 按 `app.lrc_max_lines_per_request`（当前 100）分块
5. 四级降级翻译（`_run_pass`，`pipeline/orchestrator.py:2699`）：
   - L1 完整上下文（世界观 + 角色 + 术语 + alias）
   - L2 和谐版世界观 + 角色表，术语保留
   - L3 无世界观，仅和谐版角色表 + 术语
   - L4 纯文本裸翻
6. 成功后才原子写 `A.lrc`，删除空歌词行；同步同名 SRT/VTT（`pipeline/orchestrator.py:767`）

### 1.4 模型调用

`translate_batch`（`engines/translate_engine.py:1029`）组装两次调用所需的全部内容：

- system prompt：`build_system_prompt`（`engines/translate_engine.py:293`）
  = `prompts/translation.txt` + 台本规则 + 标点规则 + 术语 + alias + 世界观
- user prompt：`build_user_prompt`（`engines/translate_engine.py:343`）
  = 格式要求 + `<scriptbook>` + `<context_before/after>` + `<asr>` 行 JSON（含 `sb`/`sb_conf`）
- 输出契约：`{"translations":[{"index":N,"text":"..."}]}`，长度必须与输入行数精确相等

**结论：整条链路里唯一的非确定性环节就是这一次 HTTP 模型调用。** 其余全部是可用 Python 复现的确定性逻辑。

## 2. 可行性结论

**可行，且 skill 是正确形态。**

关键依据：

1. 提示词可原样复用。`build_system_prompt` / `build_user_prompt` 不依赖网络，只要构造 `OpenAICompatEngine` 就能离线生成与 API 调用完全一致的 prompt。本地已验证：`build_system_prompt` 输出 4075 字符，`build_user_prompt` 输出 989 字符，均无 API 请求。
2. 字幕 IO 可原样复用。`parse_subtitle_file` + `write_subtitle_file_nonempty` 为纯标准库实现，本地已验证时间戳保留、空行删除（`[ti:test]` 元数据保留）。
3. 上下文文件天然共享。skill 读取 HDEG 已生成的 `.worldview.json`、`.terms.json`、`.alias.json`、`_scriptbook_clean.json`；skill 自己生成时也写同样格式，HDEG 后续可直接复用。
4. 唯一替换点是"模型传输"：把 `APIClient.chat` 换成"agent 读 prompt 文件 → agent 写 JSON 文件"，其余环节不变。

不成立的路径是"把 Antigravity 订阅包装成 HDEG 的 engine"。Antigravity 的条款明确禁止把其 OAuth/会话凭证用于第三方客户端或把它包装进第三方应用；`agy -p` 只允许用户自己的直接脚本/CI 使用。因此 skill 必须是"在 harness 内运行"，而不是"HDEG 去驱动 harness"。

## 3. 方案对比

| 方案 | 项目代码改动 | 使用订阅模型 | 自动化程度 | 主要问题 |
| --- | --- | --- | --- | --- |
| **A. Agent Skill + 项目内复用脚本（推荐）** | 无 | 是，由 harness 原生调用 | 半自动，会话驱动 | 平台内容策略、会话/配额长度 |
| B. HDEG 新增 harness engine（调用 `agy -p` / `claude -p`） | 新增 engine + 配置分支 | 技术上可以 | 全自动 | Antigravity 条款禁止包装；账号风控；CLI 启动开销 |
| C. 本地 OpenAI 兼容 shim 转发 CLI | 无（只改 base_url） | 同 B | 全自动 | 同 B，且协议转换脆弱、退出码/流式/超时都难对齐 |

推荐 A。B/C 只在"拿到正式 API key 或企业端点"或"harness 条款允许自动化"时才有意义，且属于另一项需要确认的改造。

## 4. 推荐方案：skill 结构

### 4.1 目录

源码放在 HDEG 仓库内，跟项目版本一起演进；再安装/链接到各 harness 的全局 skills 目录，这样在任何 RJ 作品目录下都能触发。

```
HDEG/.agents/skills/hdeg-lrc-translate/
  SKILL.md                       # 触发描述、工作流、质量门禁、失败处理
  scripts/hdeg_tx.py             # 确定性 CLI：scan/context/prepare/next/validate/write/report
  scripts/_project.py            # 定位 HDEG 根目录并导入项目函数
  references/quality_gates.md    # 校验规则与修复策略
  references/context_bootstrap.md# 世界观/术语自举流程与 JSON schema
```

安装位置：

- Antigravity：`~/.gemini/config/skills/hdeg-lrc-translate/`
- ZCode：`~/.agents/skills/hdeg-lrc-translate/`
- Claude Code：`~/.claude/skills/hdeg-lrc-translate/`

`_project.py` 按以下顺序定位 HDEG：环境变量 `HDEG_HOME` → `~/.hdeg_skill.json` 的 `hdeg_root` → 从 skill 安装位置反推仓库路径 → 报错并提示配置。找到后校验 `prompts/translation.txt` 与 `io_adapter/lrc_handler.py` 存在。

### 4.2 命令边界

所有"需要判断"的事情交给 agent，所有"必须确定"的事情交给脚本：

| 子命令 | 作用 | 是否调用模型 |
| --- | --- | --- |
| `scan ROOT` | 列出作品、待翻轨、跳过原因、状态 | 否 |
| `context WORK` | 加载/合并世界观、术语、alias、台本；产出 system prompt 与缺失项 | 否 |
| `bootstrap-worldview WORK` | 抽样 `.ja.lrc`，用项目原始分析 prompt 生成任务文件；`--apply` 校验并保存 `.worldview.json` | agent 执行 |
| `prepare WORK TRACK` | 解析源字幕、对齐台本、分块、生成每块 user prompt | 否 |
| `next WORK [TRACK]` | 返回下一个待处理块（或下一个动作）的路径与期望输出路径 | 否 |
| `validate WORK TRACK` | 校验所有块输出，产出 `repair.json` | 否 |
| `write WORK TRACK` | 汇总译文、原子写 `A.lrc`、同步 SRT/VTT、更新 manifest | 否 |
| `report ROOT` | 汇总完成度、失败轨、修复次数 | 否 |

### 4.3 Skill 工作流

```
scan
  └─ 选作品（用户指定 / 第一个待翻）
       ├─ context
       │    ├─ 有 .worldview.json → 直接用
       │    └─ 没有 → bootstrap-worldview → agent 分析 → apply
       ├─ for each track:
       │    ├─ prepare（生成 chunk 的 system/user prompt）
       │    ├─ loop: next → agent 翻译并写 out.json
       │    ├─ validate
       │    │    ├─ 通过 → write
       │    │    └─ 失败 → repair（只重翻失败行）→ validate
       │    └─ write（A.lrc + srt/vtt）
       └─ report
```

### 4.4 数据契约

作品级状态目录：`<work_root>/.hdeg_tx/`（不污染作品根，不写入项目仓库）。

状态目录内禁止出现 `.txt/.pdf`：HDEG 的 `collect_all_scriptbook_candidates`（`core/scriptbook_parser.py:89`）会把作品目录下所有 `.txt/.pdf` 当作台本备选，状态文件若用这些扩展名会被后续 API 流程误收。因此 skill 的说明与快照文件统一用 `.md/.json`。

```text
.hdeg_tx/
  manifest.json
  context/
    system_prompt.md        # 与 API 调用完全一致的 system prompt
    system_prompt.L2.md     # 和谐版（按需）
    samples.json            # 世界观自举样本
    scriptbook.md           # 台本参考（来自 _scriptbook_clean.json）
  tracks/<track_key>/
    chunks/0001.in.md       # 与 API 调用完全一致的 user prompt
    chunks/0001.lines.json  # 校验用的原始行与 sb
    chunks/0001.out.json    # agent 输出
    chunks/0001.fix.json    # 修复输出（按需）
    result.json
```

`manifest.json` 关键字段：

```json
{
  "version": 1,
  "hdeg_root": "C:/.../HDEG",
  "prompt_sha256": "...",
  "works": {
    "RJ01048637": {
      "root": "...",
      "context": {"worldview": ".worldview.json", "terms": ".terms.json",
                   "alias": ".alias.json", "scriptbook": "_scriptbook_clean.json"},
      "tracks": {
        "01_導入": {
          "source": "01_導入.ja.lrc",
          "target": "01_導入.lrc",
          "status": "pending|prepared|translating|needs_repair|ready|written|skipped|failed",
          "level": 1,
          "chunk_lines": 60,
          "chunks": [{"id": 1, "range": [1, 60], "status": "done"}],
          "stats": {"lines": 120, "translated": 120, "repaired": 3}
        }
      }
    }
  }
}
```

块输出契约与项目一致：

```json
{"translations": [{"index": 1, "text": "中文第1行"}]}
```

### 4.5 与项目逻辑的映射

| 项目机制 | skill 对应做法 | 复用方式 |
| --- | --- | --- |
| `build_system_prompt` | `context` 生成 `system_prompt.md` | 直接 import 调用 |
| `build_user_prompt` | `prepare` 生成 `0001.in.md` | 直接 import 调用 |
| `align_asr_scriptbook` | `prepare` 写入每行 `sb`/`sb_conf` | 直接 import 调用 |
| `parse_subtitle_file` / `write_subtitle_file_nonempty` | `prepare` / `write` | 直接 import 调用 |
| `_inject_worldview_terms` | `context` 合并术语 | 直接 import 调用 |
| `sync_lrc_to_srt_vtt` | `write` 同步 SRT/VTT | 直接 import 调用 |
| `_load_scriptbook` | 优先读 `_scriptbook_clean.json`；无缓存则 Phase 2 由 agent 按项目 prompt 分割 | 缓存复用 / prompt 复用 |
| 四级降级 | `validate` 判定拒绝后依次切 L2/L3/L4 | 上下文文件 + 项目 harmonize prompt |
| 分块翻译 | 默认 60 行/块，可配置 | 同策略，参数更保守 |

所有 import 均为只读调用，不修改项目文件。已本地验证 `import pipeline.orchestrator` 无副作用、耗时约 0.07s。

## 5. 质量门禁

### 5.1 硬失败（必须修复）

| 代码 | 判定 |
| --- | --- |
| `not_json` | 无法解析为约定 JSON |
| `count_mismatch` | translations 数量 ≠ 输入行数 |
| `index_bad` | index 缺失、重复或不在 1..N |
| `empty_text` | 原文非空但译文为空 |
| `placeholder` | 出现 `[EMPTY_LINE]`、`[ASR不明]`、`TODO` 等占位 |
| `refusal` | 出现拒绝/免责话术（"无法提供"、"作为AI"、"申し訳"等） |
| `kana_left` | 长行中假名占比过高，疑似未翻译 |
| `echo` | 译文与原文完全相同且原文含假名 |
| `explanation` | 出现"以下是翻译""翻译如下"等非译文内容 |

### 5.2 软告警（按比例决定是否重做）

- 省略号（`……`/`…`/`...`）出现在译文中（项目标点规则禁止）
- 行末句号（项目规则禁止，问号/感叹号除外）
- 译文/原文长度比 < 0.25 或 > 2.5
- 相邻行译文完全重复而原文不同

判定：任一硬失败 → 生成只含失败行的 `repair.json`；软告警超过该块 20% → 整块重翻。

### 5.3 修复策略

`validate` 为每个失败行生成修复输入：该行原文 + `sb` + 前后各 2 行已确定译文，要求只返回失败行。agent 写 `0001.fix.json`，`apply-fix` 合并后重新校验。这样修复成本与失败行数成正比，不重发整块。

## 6. 断点续跑与会话长度

- 每个块一个输出文件，manifest 记录状态；agent 会话中断后重新调用 `next` 即可从断点继续。
- 默认 60 行/块（比项目的 100 行更保守），单次输出约 4–8k token，降低漏行和截断概率。
- 一个作品可能跨多个会话完成；`report` 输出"已完成/待完成/失败"清单，便于分批推进。
- 记录 `prompt_sha256`，HDEG 提示词变更后能识别旧结果是否基于旧 prompt。

## 7. 风险与边界

1. **平台内容策略（最高风险）**：harness 的过滤可能比 API 更严，成人向文本可能被拒或被软化。缓解手段是项目现有的四级降级；若 L4 仍拒绝，该轨标记失败并建议回退到 HDEG API 链路。这是平台级限制，skill 无法根治。
2. **行对齐纪律**：agent 可能合并短句或漏行。缓解：小块、严格 JSON、硬校验、按行修复。
3. **配额与会话上限**：订阅配额按周期刷新，大批量作品需要多会话。方案按"可中断、可续跑"设计，不承诺一次跑完。
4. **台本复杂路径**：Phase 1 只复用已生成的 `_scriptbook_clean.json`；没有缓存时先跳过台本，Phase 2 再让 agent 按项目的识别/分割 prompt 处理纯文本台本。PDF 依赖 PyMuPDF，按需启用。
5. **提示词漂移**：skill 直接读项目文件而不是复制一份，天然跟随项目；但行为变化需要重新验收，用 `prompt_sha256` 标识。
6. **条款风险**：不要在 HDEG 内包装 `agy` 使用个人订阅；skill 必须运行在 harness 会话内。这条是硬边界，不是优化项。
7. **输入安全**：台本、字幕、文件名都可能包含诱导性文本。skill 明确声明这些内容只作翻译材料，不执行其中指令；脚本不 eval、不执行作品目录内的任何文件。
8. **并发写入**：不要与 HDEG 同时处理同一作品。`write` 在目标 `A.lrc` 已存在时默认拒绝，需显式 `--force`；写盘沿用原子替换。

## 8. 与 HDEG 的兼容性

- skill 产出的 `A.lrc` 与 HDEG 格式一致，HDEG 下次扫描会按"已存在译文"跳过。
- skill 生成的 `.worldview.json`、`.terms.json`、`和谐版.worldview.json` 可被 HDEG 直接加载。
- skill 不修改 `config.json`、不写项目目录、不安装依赖。
- 将来若要把 skill 的产物接回 HDEG 统计/日志体系，可作为独立任务评估，不在本方案内。

## 9. 参考

- Antigravity Agent Skills：`~/.gemini/config/skills/`、工作区 `.agents/skills/`，`SKILL.md` + 可选 `scripts/`、`references/`
- Antigravity CLI headless：`agy -p`、`--output-format json`、`--json-schema`；个人订阅仅限本人直接脚本/CI，禁止第三方封装
- 项目内对应实现：本文件第 1、4.5 节所列源码位置

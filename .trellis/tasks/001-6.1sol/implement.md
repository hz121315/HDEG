# 001-6.1sol 实现与验证

> 2026-11-19：HDEG准备模式与纯消费Skill已实现；状态review，真实API/GPU/Agent端到端待验收。

## 已完成

- HDEG `app.prepare_only=false` 默认配置、示例、发布缺省补齐；开启时在旧多根/翻译逻辑之前独立分流。
- 新增 `pipeline/translation_prepare.py`：转录、精确待译范围归组、严格缓存/台本适配、用途API世界观、有效术语/alias、规范整理、对齐、固定块、完整prompt及润色模板、版本化资产/摘要与manifest。
- 原版与normalized两套资料；normalized只允许空白/格式变化，非空白字符、身份/年龄/关系/同意、角色标识和固定译名不变。旧脱敏缓存不复用。
- 缺转录源、空有效字幕、坏缓存/模型失败不READY；开始PREPARING、失败带stage/reason_code，其他作品可继续；输入前后摘要核验，旧批次不删。
- Skill已改纯消费，仅scan/open-track/submit-chunk/submit-polish/fail-track；删除资料生成、脱敏、API配置/全局配置读取/词典/引擎和对齐依赖。
- Skill保留strict ID/类型、源摘要、固定上下文、终态/锁/no-clobber、可选润色、本地字幕同步；失败日志在Skill.runtime，不改准备包。
- 文档及本任务档案同步，原hdeg-translate不改。没有修改本地真实config.json或凭据，没有构建、提交或推送。

## 文件落点

HDEG：`pipeline/orchestrator.py`、新 `pipeline/translation_prepare.py`、`io_adapter/config_loader.py`、`config.example.json`、`build_release.py`、README/使用说明及三个新增测试文件。

Skill：`C:/Users/Administrator/.gemini/config/skills/hz-rj-translate/` 内CLI、配置、SKILL/workflow、UI元数据和tests。Skill位于用户全局目录，不在HDEG Git工作树。旧prompts/translation_harness.txt保留为历史未引用文件，不作为消费端数据来源。

## 实际验证

```text
.venv/Scripts/python.exe -I -B -m pytest -m "not live" -o addopts= -q
```

最终结果：**187 passed, 1 deselected, 1 warning**（12.92秒）。排除项是需要真实凭据的live测试；警告为既有pyopenjtalk/pkg_resources弃用。

新增测试：
- `tests/test_translation_prepare.py`：33项生产端数据/配置/空源/失败/复用/保真/调试副本/输入变化等。
- `tests/test_prepare_routing.py`：7项分流、缺省/false和非法字符串开关，证明关闭不走新入口。
- `tests/test_prepare_skill_handoff.py`：2项真实导出函数→消费CLI→两块时间轴写盘；original与normalized+润色，准备包/源前后不变。默认导出指纹自动生成也已验证。

```text
.venv/Scripts/python.exe -I -B -m unittest discover -s C:/Users/Administrator/.gemini/config/skills/hz-rj-translate/tests -q
```

结果：**56 tests，OK**（5.851秒）。独立manifest夹具，socket和准备/API模块导入被禁止，覆盖真实main消费入口、scope、坏版本/摘要、缺资产/源变化、原版/normalized快照、strict ID、终态、防覆盖/force、polish与字幕同步。输出中的 `fixture failure` 是故障注入预期警告，不是测试失败。

本轮新增测试只用临时合成数据与固定假响应；旧非live回归沿用项目现有离线mock夹具，不调用计费API或GPU。未做真实模型翻译、真实转录或真实API准备质量验收。

### 独立复核修复

- 局部准备包后扩大scan范围，未入包JA此前漏报：现在明确UNPREPARED并计数；已有目标仍跳过。
- 有限事实正则不能阻止中文数字年龄变化：规范化收紧为所有非空白字符一致，避免把提示要求当作语义保真证明。
- 旧台本识别debug会打印key片段：仅准备adapter使用关闭debug的局部ctx/pr副本，不改变共享上下文或旧模式。
- 无转录源、导出空字幕、准备期间输入变化、假api_calls=0等边界已补齐；控制台输出模式、READY路径、失败阶段和汇总。

## 静态检查与未完成项

- 已做AST、JSON、UTF-8无BOM、Skill元数据与引用、Git差异格式检查。
- 官方Skill quick_validate.py仍缺PyYAML；未安装依赖，不冒称官方校验通过。按实际简单frontmatter补做标准库检查。
- 真实API用途继承、PDF/台本质量和Antigravity模型翻译需后续授权验收。
- 原旧模式台本/PDF/NSFW历史机制不在本轮重构；准备包READY只表示结构和摘要有效，不证明语义准确。
- 无token预算自适应、无自动resume；force不提供与任意外部writer事务隔离。manifest单文件发布不是多文件事务或强杀安全保证。
- 旧Skill生成类命令和旧会话与新模式不兼容；用户需HDEG重新准备，不自动删除旧数据。

## 启用

在用户实际使用的HDEG配置中加入 `app.prepare_only=true`，按原入口处理目录。确认每作品READY后，在反重力新会话调用hz-rj-translate，指定original或normalized；消费侧不执行任何资料生成。

保持关闭或缺省时原HDEG继续原逻辑。本轮没有代替用户开启本地配置，避免意外改变下次运行行为。

## 历史

前一版独立Skill曾通过32项离线契约和本地读音对齐冒烟；该版由Skill准备上下文，已被本轮生产/消费拆分替代。此历史结果不作为当前版本新增能力的证据。

## 2026-11-19 术语命名迁移（本次）

- 本次仅进行 rename：相关文档命名统一为“脱敏”，更新用途字段、提示词与日志路径，并补充迁移兼容说明；original/normalized 的字段及规范整理语义不变，旧流程和策略事实不变。
- 源码、示例配置、发布引用、统计/日志、相关测试及说明均已同步；本地真实配置和凭据、旧Skill、其他任务及历史产物未改。
- 新字段存在即优先（包括空值），缺新字段时只读兼容旧字段；缓存新文件优先，只有新文件不存在时读取旧缓存。旧公开导入符号仍为兼容别名，不自动移动或删除用户文件。
- 最终离线回归：HDEG **216 passed、1 deselected、1 个既有弃用警告**；Skill **56 tests、OK**。新增缓存/符号兼容11项和配置兼容18项，源文件、JSON、AST、UTF-8与差异检查通过。未调用真实API、未构建或提交。

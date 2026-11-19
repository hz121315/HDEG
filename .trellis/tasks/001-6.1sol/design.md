# 001-6.1sol 生产／消费设计

## 架构

```text
HDEG app.prepare_only=true
  → 原转录+JA留档
  → 精确范围按作品准备
  → 原版资料+保真规范整理版
  → 台本对齐/固定分块/完整静态提示
  → .hdeg-translation/manifest.json READY

hz-rj-translate
  → 只读校验包/源/范围
  → 开轨固定original或normalized快照
  → 宿主逐块翻译（可选译文润色）
  → 严格校验/整轨安全提交
```

开关默认false；新分支在run_pipeline现有多根和无主key分支之前，只转入独立模块，不改变旧翻译引擎/API/四级机制。没有UI改造或新依赖。

## HDEG准备模块

`pipeline/translation_prepare.py` 提供 `run_prepare_only(roots, ctx, progress_callback=None)`；纯本地 `export_prepared_work(...)` 可供隔离测试和明确上下文已就绪的调用。

- 输入先规范化、去重并检查目录；原规则转录，收集所选根内无目标JA，按作品相对键归组，不从RJ根重新扩大轨范围。
- 资料共享可读取作品台本/世界观，但manifest的轨列表仅为本次精确范围。
- 优先严格读取有效缓存；台本使用既有识别/清洗/分割算法的独立适配，关键失败不降为空；alias仅读取。
- 世界观按用途有效配置生成；术语按作品、世界观补充、配置补缺保持优先级。规范整理API只用保真规则，不调用旧脱敏输出约束或信任旧缓存。
- 不调用字幕翻译函数；生成prompt时可使用原引擎的本地build方法与读音聚类，但不chat。
- 合法空alias/无台本/FreeTalk明确标记，失败作品manifest为FAILED，其他作品可继续。

## schema v1

```text
作品/.hdeg-translation/
  manifest.json
  data/<prepare_id>/context.original.json
  data/<prepare_id>/context.normalized.json
  data/<prepare_id>/tracks/<轨键hash>.json
  data/<prepare_id>/prompts/<轨键hash>.<版本>.<块>.json
  data/<prepare_id>/polish.<版本>.json
```

Manifest：`schema_version=1`、`status`、`prepare_id`、`work_root`绝对路径、规则版本/摘要、时间、`input_fingerprint`、两个context引用、轨条目及`assets`全部路径→SHA256。

Context：`worldview:dict|null`、`terms:dict[str,str]`、`alias:list`，可附来源和合法缺省状态；两套既定译名保持一致。

Track：作品内相对轨键/source/target、源SHA256、有效原文、对应time_tags、有效行在original_lyrics中的索引、顺序完整的0基chunks、台本与对齐、两个版本逐块prompt引用和polish模板引用、固定润色/字幕输出计划。

Prompt JSON：`system_prompt`、`user_prompt`。Polish JSON：`system_prompt`、`user_prefix`、`user_suffix`，由空lines占位拆出；消费者只填入实际draft。

所有资产引用限制在包内，相对POSIX路径；源/目标限制在作品内，target必须为源对应同名LRC。包不得含配置凭据、请求头或完整API配置。

## 发布、复用与失败

先标记PREPARING，写独立批次并核验，再原子发布READY manifest；失败为FAILED，不让旧READY冒充当前成果。旧批次不自动清理。manifest发布是单文件原子替换，不保证多文件事务或强杀安全。

复用需输入范围、源摘要、上下文来源/设置/提示和规则摘要一致，资产完整。消费者对所有资产再次验证；开轨前检查读取期间未变化，快照后不混用后续批次，但每次提交仍核验源。

规范整理只允许空白/格式调整，所有非空白字符一致；角色/显式事实/结构仍单独强校验。不宣称语义改写后的自动保真，真实台本对齐等质量仍需样本抽检。原台本/PDF算法限制不因包READY消失。

## Skill消费边界

只保留scan/open-track/submit-chunk/submit-polish/fail-track。仅读技能config中的可信HDEG根以使用纯字幕IO，不读取HDEG全局配置，不导入API/翻译引擎/对齐/台本模块。

Scan按用户scope枚举准备包或上级作品包并过滤轨；缺失/失败/失效明确报告。Open核验schema、资产、源/时间轴/分块，默认original，可显式normalized，不支持旧harmonized或临场重新分块/去台本。

每块提示由HDEG预生成，Skill只附上前一块最多8行译文JSON；可选润色填draft。这是运行时数据拼装，不是资料生成。

Strict JSON完整唯一整数ID、非空字符串，不打捞缺项。会话锁+目标锁，唯一会话、源摘要、OPEN/NEED_POLISH/COMMITTED/FAILED_STOPPED状态边界。目标默认暂存后硬链接no-clobber发布，force仅明确且目标摘要未变；不保证任意外部writer事务隔离。字幕同步为本地适配，不导入整个orchestrator。

准备包只读；旧会话因consumer schema不兼容明确停止，不删旧文件。没有自动resume、四级重试、拒答换版本或换会话。

## 取舍与兼容

- 新模式完整准备后交接，初版不保留转录/翻译流式交叠。
- 旧prepare/ensure/save类Skill命令删除，无准备包旧用法需要HDEG重新准备。
- 分块、润色和字幕输出选项在HDEG准备时固定；Skill侧只保留并发约定和可信路径。
- 保留原hdeg-translate不改；只改hz-rj-translate及本任务档案。

## 参考

- [Python os.replace](https://docs.python.org/3/library/os.html#os.replace)：原子替换只约束单次同文件系统重命名，不等于多文件事务。
- [Agent Skills格式](https://agentskills.io/specification)：Skill打包及资源引用。
- [Antigravity Skill支持](https://antigravity.google/docs/skills?tab=ide)：全局目录与发现机制，不授予模型权限或内容豁免。

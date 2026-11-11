# 001 参数记录

以下为任务相关参数，不是 config.json 的全部字段。默认值依据本轮源码静态核查，不代表运行目录中用户实际配置。

## 调度、识别与输出

| 完整键 | 默认／示例值 | 实际意义与边界 |
| --- | --- | --- |
| app.stream_workflow | true | 流式总开关；all_at_once 仍回退整批 |
| app.translation_parallel | 10 | 全局音轨翻译池大小，不是作品数；0 的首轮与重试处理不一致，见 verification |
| app.parallel_works | 10 | 同时分析／翻译／等待完成的作品数，最小钳制为 1 |
| app.work_progress_windows | true | 创建最小化纯显示窗口；当前没有实际进度写入接线 |
| app.rj_lookup_depth | 4 | 保持旧的从传入路径开始 depth+1 次检查语义；起点为文件时含文件自身，不是先严格验证目录 |
| app.skip_hanhua_files | false | 期望控制“汉化／漢化”后缀跳过；收集候选时存在开关失配 bug |
| app.srt_vtt_resync | true | 控制视频来源补生成缺失 SRT/VTT；已有字幕仍走旧同步函数，不完全等于“总开关” |
| app.lrc_max_lines_per_request | 100 | 分块 API 行数，0 表示整轨一次；目标文件仍整轨完成才写 |
| app.transcribe_settle_seconds | 2.0 | size+mtime 不变的持续时间，不是引擎完成确认 |
| app.transcribe_poll_interval | 0.5 | 采样间隔；watcher 最低钳制为 0.05 秒 |
| app.polish_after_translate | false | 可选后编辑；提示词位于 translate_engine.POLISH_SYSTEM_PROMPT |
| app.translate_failed_max_retries | 3 | 音轨级重试轮数；上下文最小钳制为 1 |
| app.nsfw_fallback_enabled | true | NSFW 多级降级重试总开关（2026-11-10 新增，见下） |
| api.harmonize.{key,base_url,model,protocol} | 空 | 世界观和谐化专用 API；全空时复用翻译 API（2026-11-10 新增） |
| api.worldview.{key,base_url,model,protocol} | 空 | 世界观**分析**专用 API；全空时复用翻译 API；与 api.harmonize 相互独立（2026-11-11 新增） |
| prompts.harmonize_system_prompt_file | 空（发布件指向 prompts/harmonize.txt） | 和谐化自定义规则 prompt（UTF-8）；**替换**内置改写规则部分，内置【输出要求】始终附加；缺失/为空/读取失败回退纯内置（2026-11-10 新增，2026-11-11 语义从"拼接"改为"替换"） |
| prompts.worldview_system_prompt_file | 空（发布件指向 prompts/worldview.txt） | 世界观分析自定义规则 prompt；同上替换语义（2026-11-11 新增） |
| prompts.system_prompt_file | prompts/translation.txt（发布件/模板/本地） | 翻译“可自定义基础设定”；台本优先级与字幕标点规范为 `translate_engine.py` 内置常量，**始终追加、不可被文件替换**。文本已抽离为 prompts/translation.txt（源仓库/发布目录/打包内嵌三处同源）。为空或文件缺失时回退 exe 内嵌副本；相对路径先按 CWD、再按程序目录解析。术语/alias/世界观仍由 build_system_prompt 追加（2026-11-11 抽取；同日台本/标点写死内置） |
| app.api_preflight | true | 请求可用性预检，不是翻译任务 |
| app.delay_translate_to_offpeak | false | 等待固定定价时段后派发 LLM；不能保证当前服务商半价 |

RJ 正则仍为 `RJ\d{6,}`、忽略大小写、匹配目录名中的子串；本次未确认新增自定义正则。精确行为见 file_scanner.py。

## NSFW 多级降级重试（2026-11-10 新增）

背景：部分 API 对成人向内容做道德审查，翻译返回空白或拒绝字符串，最终 `.lrc` 为空白。

单音轨内按整轨为单位逐级降级，块级快速失败（任一 ≥5 行的块 `parsed_count==0` 即终止本级）：

| 级别 | 携带上下文 | 备注 |
| --- | --- | --- |
| 第1级 | 原版 worldview + characters + terms + alias | 与旧行为一致 |
| 第2级 | 和谐版 worldview（含和谐 characters） + terms + alias | 依赖和谐版文件 |
| 第3级 | 无 worldview，仅和谐版 characters + terms + alias | 依赖和谐版文件 |
| 第4级 | 纯文本（无任何附加） | 终级，失败即跳过 |

- 失败判定覆盖三类：API 异常、整块 `parsed_count==0`（拒答/非 JSON）、译文全空（`parsed_count>0` 但全空串）。
- 和谐版由 LLM 生成（`harmonize_worldview_with_llm`），落盘 `<RJ组根>/和谐版.worldview.json`，不覆盖 `.worldview.json`；同组复用（per-wkey 锁 + 双重检查防并发重复生成）；生成失败时第2/3级直接跳过。
- 每级失败向音频所在目录 `log/<音频同名>.log` 追加记录（时间戳 + 请求序号 + 原因 + raw_response 截断 4000 字）；`raw_response` 随 `TranslationResult` 返回，规避共享引擎实例字段的并发竞态。
- 第4级失败：`stats['skipped']+=1`、`retry_results` 记"多级降级后跳过: …"（不以"失败"开头，避免批式/串行外层误判待重试），不再入轮级重试队列。
- 开关关闭时仅第1级，失败走旧逻辑入轮级重试队列，不写失败日志、不生成和谐版（完全旧行为）。
- 每级（含失败级）都记 TokenUsage（`request_type='translate'`）；和谐化调用记 `request_type='harmonize'`，price 沿用全局配置估算。
- 已知边界：`api.harmonize` 独立配置仅覆盖 key/base_url/model/protocol 四项，models 注册表与 generation_params 继承全局；和谐化请求本身也可能被拒（返回 None → 跳过依赖它的级别）。

## 转录传参

| 完整键 | 默认／示例值 | 实际意义与边界 |
| --- | --- | --- |
| transcription.infer_exe | 示例 ./转录模型/infer.exe；发布改为 .\infer.exe | 相对应用目录解析 |
| transcription.model_dir | 示例 ./转录模型/models；发布 .\models | 原样写 MODEL_DIR 环境变量；相对路径与 cwd 有关联 |
| transcription.device | cuda | 传给 infer |
| transcription.compute_type | float16（2026-11-10 起发布件/模板/本地默认） | 按 9700X+32G+5070 Ti 配置；旧默认 int8_float16；auto_tune 开启时 ≥10GB 仍会改 float16（与显式一致） |
| transcription.audio_suffixes | mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv | 同时驱动 HDEG 音频发现和 infer 参数；不是 app.audio_suffixes |
| transcription.sub_formats | lrc | 管道依赖 LRC；只改为其他格式会使 watcher 找不到产物 |
| transcription.extra_args | `["--enable_batching", "--batch_size=32", "--smart_split_with_vad=false"]`（2026-11-10 起发布件/模板/本地默认） | 非空时为最终生效值，auto_tune 自动参数让位；build_release.py 的 copy_config 会强制写入这组默认值 |
| transcription.main_exe | 空 | 发布 BAT 可指定启动主程序，空为同目录 main.exe |
| transcription.auto_tune | true（代码隐式，示例未列） | 按第一张 GPU 总显存选择精度和批大小，来自夹带的既有改动，不是最初确认范围 |

当前自动批大小：≥14GB 为 32，≥10GB 为 24，≥7GB 为 16，否则 8；自动列表开启 batching、设 batch_size 并关闭 smart_split_with_vad。**非空 extra_args 不阻止 compute_type 提前被自动修改**（新默认显式 float16 与之一致，无实际冲突）。

2026-11-10 起 `float16 + batch 32 + smart_split_with_vad=false` 已成为发布件（`build_release.py` copy_config 强制写入）、`config.example.json` 模板与本地运行配置的默认值，依据用户确认的目标机规格（9700X + 32G 内存 + 5070 Ti 16G 显存），不再依赖运行时 auto_tune 或手动修改。低显存机器的回退方式见《使用说明.md》。仅适用于支持这些参数的 infer 版本；不是显存容量承诺。HDEG 不决定外部引擎的真实 batch 合并、吞吐或每句日志方式。

## Prompt/API 保留能力

- api.key、base_url、model、protocol、models 注册表和 generation_params 保留。
- prompts.system_prompt_file 可替换翻译 system 的基础角色段，术语、alias、世界观仍追加；不覆盖 JSON 格式契约、分析或润色 prompt。该基础段已抽离为 `prompts/translation.txt`（源码/发布目录/打包内嵌同源）。
- 外部 prompt 以 UTF-8 读取；相对路径先按进程 CWD 解析，不存在再按程序目录解析；读取失败回退 `prompts/translation.txt`（exe 内嵌副本）。
- 翻译 max_tokens_translate 优先于 max_tokens；小于 16384 时代码会抬至 262144，不能把任意小值当成有效限额。

## 取消或未纳入的候选

language_detect_ratio 随完成规则变更作废；rj_pattern、watchdog_timeout、多根单独进程、逐块断点等未纳入本轮最终计划。不能因历史讨论中出现过就写成已支持参数。

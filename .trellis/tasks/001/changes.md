# 001 改动与提交记录

## 历史提交（本轮从 Git 日志核实）

| 提交 | 主题／作用 |
| --- | --- |
| fd3b3a64 | 上游起点：修复 CI 集成测试环境依赖与 mock 路由 |
| 0d62ed95 | 流水线主体：文件存在性判定、watcher、源／目标分离、原子写、多根并发、同名轨键、配置与测试 |
| c9fbbfa9 | README 初次补充流水线能力与参数 |
| e494c488 | transcription.extra_args 透传 |
| 7d52c5e5 | 新增使用说明文档 |
| aac88a2a | 发布 BAT 不再直接转录；同时夹带既有 orchestrator 自动显存调参及文档删除 |
| fff95563 | 恢复被上一提交误纳入删除的使用说明、项目介绍 |
| cacf5e7c | README 整理：改动归入工作流程，解释 BAT／配置归属，移除尾部重复章节 |

会话记录这些提交曾经代理推送到 hz121315/HDEG；本轮未查询远端，不对当前远端同步状态作新声明。

## 修改落点

| 文件／模块 | 本次会话改造内容 |
| --- | --- |
| main.py | 多行 input_path.txt，多个根目录一次交给管道 |
| build_release.py | 生成支持多目录的 BAT；去除 JA→LRC 恢复、直接 infer 和 BAT 归档；最终统一转录参数到 config |
| pipeline/orchestrator.py | 新文件规则、source/target、转录 watcher 回调、流式作品调度、共享池、并发上限、视频字幕、相对轨键、extra_args |
| pipeline/transcribe_watch.py | size+mtime 稳定检测、归档回调、组就绪回调、退出强制收尾 |
| pipeline/work_windows.py | PowerShell 最小化纯显示窗口，tail 临时进度文件（实际进度写入尚未接入） |
| io_adapter/lrc_handler.py | 字幕原子写、LRC 转 SRT/VTT |
| io_adapter/file_scanner.py | RJ 查找深度配置，默认 4，保持现有 RJ 正则 |
| io_adapter/config_loader.py、config.example.json | 流水线和调度参数默认值／示例 |
| tests/conftest.py、test_stream_workflow.py | 测试配置、新增 8 个基础测试 |
| README.md、使用说明.md | 文件规则、流水线、入口／参数归属、使用说明 |
| .gitignore | temp/ 发布产物排除 |

## 2026-11-10 NSFW 多级降级重试（用户需求，本轮已实现）

| 文件／模块 | 改动内容 |
| --- | --- |
| engines/worldview_engine.py | 新增 `和谐版.worldview.json` 路径/读写、`HARMONIZE_SYSTEM_PROMPT`、`harmonize_worldview_with_llm`（LLM 改写高危设定，剥离 `_source`，结构校验） |
| engines/translate_engine.py | `TranslationResult` 新增 `raw_response` 字段，`translate_batch` 透传模型原始返回 |
| pipeline/orchestrator.py | PipelineContext 新增 `nsfw_fallback_enabled`/和谐引擎缓存/per-wkey 锁；新增 `_get_harmonize_engine`、`_load_harmonize_prompt`、`_ensure_harmonized_worldview`、`_write_translate_failure_log`；`translate_one_lrc` 重构为多级降级（`_run_pass` 闭包 + 4 级循环，块级快速失败、全空判定、失败日志、耗尽跳过） |
| engines/worldview_engine.py（追加） | `harmonize_worldview_with_llm` 新增 `extra_system_prompt` 参数：自定义前置 prompt 拼接在内置规则之前（应对和谐化请求本身因血腥暴力被拒），内置输出格式约束保留兜底 |
| io_adapter/config_loader.py | default_config 增加 `app.nsfw_fallback_enabled: true` |
| config.example.json | `app.nsfw_fallback_enabled` 示例、`api.harmonize` 空值子段、`prompts.harmonize_system_prompt_file` |
| utils/token_tracker.py | TYPE_CN_MAP 增加 `harmonize: 和谐化` |
| tests/test_nsfw_fallback.py | 新增 12 个离线用例（成功/拒答降级/全失败跳过/开关关闭旧行为/无世界观直降第4级/全空触发/异常触发/和谐化单元/生成与复用/路径读写） |
| 使用说明.md | 参数表新增条目 + "NSFW 多级降级重试"章节 + 文件说明补充 |

验证：`pytest -m "not live"` 66 passed（含新增 12 项），AST/py_compile 静态检查通过。未跑真实计费 API、未打包。

## 2026-11-10 发布默认转录参数（用户确认目标机 9700X + 32G + 5070 Ti）

- `build_release.py` copy_config：transcription 段强制写入 `compute_type=float16` +
  `extra_args=["--enable_batching", "--batch_size=32", "--smart_split_with_vad=false"]`
  （与既有 infer_exe/model_dir 发布相对路径改写并列；extra_args 非空时运行时 auto_tune 让位）。
- `config.example.json`、本地 `config.json`（gitignored）transcription 段同步为同组默认值。
- `使用说明.md` / `README.md`：默认参数说明、低显存回退方式；`.trellis parameters.md` 同步记录。
- auto_tune 代码未动：显式 extra_args 时自动参数已让位，显式 float16 与 ≥10GB 自动改写一致。

## 2026-11-11 世界观分析独立 API/Prompt + 发布件 prompts/ 目录

- `engines/worldview_engine.py`：两个内置 prompt 各拆为「规则部分（可替换）」+「【输出要求】（内置保留）」——
  `WORLDVIEW_ANALYSIS_RULES_PROMPT`/`WORLDVIEW_ANALYSIS_OUTPUT_REQUIREMENTS`、
  `HARMONIZE_RULES_PROMPT`/`HARMONIZE_OUTPUT_REQUIREMENTS`，旧常量 `*_SYSTEM_PROMPT`/`WORLDVIEW_ANALYSIS_PROMPT`
  以拼接形式保留兼容。`harmonize_worldview_with_llm` 自定义语义从"前置拼接"改为"**替换规则部分**"；
  `analyze_worldview_with_llm` 新增 `extra_rules_prompt` 参数，返回值扩为三元组（含 `fail_info`）。
- `pipeline/orchestrator.py`：`_get_context_engine` 泛化（harmonize/worldview 通用，api.worldview 接入）；
  `_load_rules_prompt` 泛化（worldview/harmonize 两键独立）；世界观分析失败写 `<作品根>/log/worldview.log`
  （原因+原始返回，与翻译失败日志同目录约定）。
- `build_release.py`：发布件生成 `prompts/` 目录（`worldview.txt`+`harmonize.txt`，导出规则文本），
  config 默认指向这两个文件；`api.worldview` 占位补齐。
- 测试：`tests/test_nsfw_fallback.py` 更新 1 项+新增 4 项（prompt 拆分断言、fail_info、worldview 引擎/失败日志、双键独立），
  全量 72 passed。

## 环境与打包经历（非业务代码变更）

- 最初 GitHub 直连失败，先下载快照，后重试取得 Git 仓库；后续 origin 指向用户仓库 hz121315/HDEG。
- 建立 Python 3.10 虚拟环境，安装运行、测试和 PyInstaller 工具。环境曾丢失后重建，期间出现不含 pyopenjtalk 的 81MB 左右成品，用户要求恢复完整依赖。
- 官方 pyopenjtalk 当时无匹配的 Windows wheel，现场编译缺工具链；曾尝试 prebuilt／plus，并定位 pkg_resources、numpy ABI、词典在线下载失败。
- 最终本地重包 wheel：pyopenjtalk 0.3.0，二进制来自 pyopenjtalk-prebuilt，词典取自 plus wheel，固定 numpy<2、setuptools<81，并安装 pkg_resources 冻结时所需辅助包。
- 本地 wheel 和构建脚本曾保存在项目外 hdeg-wheelhouse。**它们不是仓库保证提供的资源，本轮未核实其可用性，干净 Windows 安装仍需单独验证。**
- 完整发布主程序历史记录为约 117–119MB；不能依据成品大小断定依赖齐备。历史曾做 frozen g2p 探针，但每次后续构建并非都重新验证。
- 产物位置最终为项目根 temp/，文档随包提供。构建曾多次覆盖本地 config.json、自动改写 main.spec；后续操作应先检查并保留用户配置，不能重复覆盖。

## 回溯纠错

- 多次仅修源码模板、没有更新用户运行的旧 BAT，引起“新 main.exe + 旧 BAT”混用。
- 流式 Python 实现并不等于发布入口已接通；最终统一 BAT 入口才消除整批先转录的阻塞。
- 曾用 git add -A 纳入不相关改动与删除；已发生的提交保留记录，不重写历史。本次只记录，不提交新业务变更。
- 已跟踪文档后续更新无需反复 git add -f；忽略规则不阻止跟踪文件的正常修改提交。此前会话说法不准确。
- W 编号是可复用 worker 线程编号，不是固定音轨 ID；并发日志可能交错，不据日志相邻推断上下文混用。

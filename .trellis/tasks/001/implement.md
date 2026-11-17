# 001 实现落点与接续清单

本文件是已发生实现的回溯索引，不是宣称所有功能已验收的执行报告。

## 已落地的代码骨架

- [x] 新增流式、并发、文件识别相关配置默认值和示例。
- [x] 新增 watcher、作品窗口模块。
- [x] 翻译源／目标分离、字幕原子写、视频字幕生成函数。
- [x] 相对路径轨键、ASR索引、多根共享调度。
- [x] extra_args 透传。
- [x] 发布 BAT 最终移除直接转录／占位恢复，统一入口至 main.exe。
- [x] 增加8个基础用例，历史记录离线54项通过。
- [x] 多次打包到项目 temp/，历史提交已包含README使用解释。

## 不能打勾的验收项

- [ ] 真实单根多RJ与多根交叠转录／翻译的时间顺序及上限。
- [ ] infer 输入范围与pending一致，已有JA和汉化文件不被外部引擎重转。
- [ ] 归档失败不误报组就绪、写入暂停不归档半截字幕。
- [ ] API失败重试保留source_path，重试耗尽有最终状态。
- [ ] 多根入口保留无Key／目录校验等公共契约。
- [ ] 同名轨旧缓存消歧与共享引擎线程安全。
- [ ] 进度窗实际更新、逐轨／作品日志及报告完整。
- [ ] 视频字幕开关、skip统计、汉化过滤一致。
- [ ] 干净环境可复现完整pyopenjtalk成品；最新exe真实运行验证。

## 2026-11-17 追加实现（对应提交 2fcc74ca..46d709c7）

- [x] NSFW 四段降级重试 + 和谐版文件生成/复用 + 失败日志（音频同名 log）。
- [x] `api.worldview` / `api.harmonize` 独立端点与 `generation_params`；两套规则 prompt 可自定义。
- [x] 翻译基础 prompt 抽离 `prompts/translation.txt`；台本/标点写死内置；发布件默认引用与 exe 内嵌。
- [x] `parsed_count` 单次调用局部返回，修复共享引擎并发误判；内部重试计数与所采用响应一致。
- [x] 失败请求诊断：时间/模型/协议/记录粒度/最终参数白名单，覆盖翻译、worldview、harmonize。
- [x] `extra_args` 引擎配置键分流到临时 `generation_config`，原配置合并保留。
- [x] 默认转录参数改为 smart VAD 六项组合；打包保留用户显式 `extra_args`。
- [x] 测试：新增 `test_nsfw_fallback.py`、`test_translation_prompt.py`、
  `test_failure_log_diagnostics.py`、`test_translation_count_isolation.py`、
  `test_api_request_diagnostics.py`、`test_transcription_engine_args.py`；离线全量 133 passed。
- [x] 打包验证：frozen exe 冒烟（prompt 内嵌、pyopenjtalk、计数隔离、请求诊断、参数分流）。
- [x] 根目录 `release/` 入库回滚；仓库不保存 exe，打包输出仍为 `temp/`。

## 仍需真实环境验收（不能打勾）

- [ ] 默认 smart VAD 六项参数在真实音频上的识别质量/耗时/显存占用对比。
- [ ] 多音轨并发下四级降级、和谐版复用、失败日志的真实端到端表现。
- [ ] Magpie/Antigravity 的 `the reply ended before it was complete` 502 根因（外部服务）。
- [ ] 发布件预置 `main.exe` 的真实双击/拖拽运行（仓库不包含 exe）。
- [ ] verification.md 中其余 001 原始缺口（进度窗、流式日志、重试源路径、归档边界等）。

## 接续工作顺序

1. 先读 README.md、prd.md、design.md、parameters.md、verification.md，确定用户接受的存在性规则，不恢复早期语言判定。
2. 检查 git status，区分用户改动；不要再次使用 git add -A 纳入不相关删除或本地配置。
3. 对 verification 中的数据损坏／漏译风险先出修复方案，涉及通用组件和架构扩大必须再确认。
4. 以假infer和mock API建立调度、归档、重试回归，再修相应局部实现；不先用真实计费API试错。
5. 修进度与日志接线，补发布入口和参数一致性测试。
6. 用户要求打包时才构建；先检查运行配置、temp内容，保留用户凭证与定制项，不覆盖用户文件。
7. 用户明确要求并确认远端操作后才提交／推送；提交前核对目标仓库与文件清单。

## 历史测试命令（本轮未执行）

```bash
.venv/Scripts/python.exe -m pytest -m "not live"
.venv/Scripts/python.exe -m pytest tests/test_stream_workflow.py
```

历史发布入口：`.venv/Scripts/python.exe build_release.py`。注意其内部会调用 PATH 中的 pyinstaller，且写发布目录及spec，不能视为纯检查命令。

## 本轮记录验收

- 001任务目录、元数据、PRD／设计／实现／变化／参数／验证文档齐备。
- 所有新文档采用UTF-8无BOM，不含API key、数据库口令或代理凭证。
- 不触发Trellis CLI初始化、不创建虚假运行脚本和session状态。
- 不修改业务代码、不构建、不推送。

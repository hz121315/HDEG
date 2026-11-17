# 001 · HDEG 改造回溯

> 基线：`cacf5e7c`；最近同步：`c6b499c9`（2026-11-17）。状态：**持续开发，自动化测试通过，待真实环境验收**。本记录于 2026-11-10 依据会话、Git 提交及源码静态核查生成，11-17 补充后续改造进度。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [prd.md](prd.md) | 最终需求、范围、用户确认过的行为 |
| [design.md](design.md) | 数据流、架构边界、决策演变与取舍 |
| [implement.md](implement.md) | 实现落点、后续执行与验收清单 |
| [changes.md](changes.md) | 提交时间线、打包和依赖处理记录 |
| [parameters.md](parameters.md) | 新增或调整的配置、实际默认值与限制 |
| [verification.md](verification.md) | 历史验证、本轮静态证据、已知问题及待验证项 |

## 30 秒接手说明

本任务希望把集成版的“转录完一部立即翻译、实时留存、断点续跑”迁入当前 HDEG，但不复制它的每作品独立翻译进程。

最终确认的文件规则：**有 A.lrc 即跳过；无 A.lrc 且有 A.ja.lrc 直接翻译；两者都没有才转录**。不再按字幕语言判定完成。

发布入口最终收敛为 BAT → main.exe → infer.exe 子进程／主进程内翻译线程池；BAT 不再抢先自行转录。多个根目录共用翻译池，同时保留台本、角色术语和世界观上下文。

**不能直接视为全部已完成**：进度窗/流式日志/重试源路径已在离线测试接通，但真实 Windows 窗口、批量 manifest 与端到端仍未验收；文件稳定判定不等于引擎确认完成，infer 仍递归整个 root。具体见 verification.md。

## 2026-11-10 至 11-17 追加能力（详见 changes.md）

- NSFW 翻译失败多级降级：完整上下文 → 和谐版世界观 → 仅和谐版角色表 → 纯文本；和谐版落盘复用，失败写音频同名日志。
- 世界观分析/和谐化支持独立 `api.worldview` / `api.harmonize`（含 `generation_params`）与独立规则 prompt；翻译基础 prompt 抽离为 `prompts/translation.txt`，台本/标点写死内置。
- 共享翻译引擎的 `parsed_count` 改为单次调用局部返回，修复并发误判。
- 失败请求记录时间、实际模型、协议及最终生成参数白名单。
- `extra_args` 中的 `--vad_filter`/`--no_speech_threshold`/`--beam_size` 自动合并进引擎 `generation_config.json5`；默认转录参数改为 smart VAD 六项组合。
- 根目录 `release/` 曾尝试入库，因公开 fork 禁止上传新 LFS 对象且 exe 超过普通 Git 单文件上限，已回滚；程序不随仓库分发。
- 流式路径补齐重试源路径、最终失败收尾、skipped 去重、同名轨日志隔离、跨作品状态隔离、逐轨/作品日志与块级进度窗回调（2026-11-17 续开发，离线 145 passed）。

## 文档维护约定

本档案随业务提交同步更新；不再沿用“只创建档案、不改代码”的初始边界。运行验证与真实 API/GPU 结论仍以 verification.md 的日期与证据为准。

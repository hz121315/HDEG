# 001 设计思路与决策演变

## 当前设计的数据流

```text
一个／多个拖入目录
  → 发布 BAT：记录多行 input_path.txt，启动 main.exe
  → main.py：解析根目录列表，调用 run_pipeline
  → 流式模式（stream_workflow=true 且非 all_at_once）
      ├─ 转录线程：逐 root 调 infer.exe（外部进程，GPU）
      │    └─ watcher：采样 LRC → 判稳 → 留档 JA → 通知作品组就绪
      ├─ 调度：API 预检／峰谷等待 → 初始已有 JA 组／就绪队列／结束补扫
      └─ 作品线程：限 parallel_works
           → 分析台本、术语、alias、世界观
           → 提交音轨到共享翻译池（translation_parallel）
           → 读 A.ja.lrc，逐请求分块翻译，可选润色
           → 完整音轨成功后原子写 A.lrc
           → 同步已有 SRT/VTT，视频来源补生成缺失字幕
      → 等待任务，失败队列重试，流式简报
```

注意：图表示当前源码骨架，不证明所有竞态和中断路径正确。尤其 watcher 产物移除、重试和收尾存在已知问题。

## 关键决策

### 不照搬集成版多进程

集成版用每作品一个 translate.exe、独立 runtime/config 和 CMD。当前方案保持一个 HDEG 编排／翻译进程，避免复制音频和重复加载 HDEG 上下文；用线程池承载网络请求。代价是不能提供进程级故障隔离，共享引擎内部状态也需专门验证线程安全。

“单进程”不代表系统只有一个进程：infer.exe 与纯显示 PowerShell 仍是子进程。它们不承担 HDEG 翻译线程池职责。

### 整 root 一次转录，按作品通知

不逐 RJ 重启 infer，以减少模型加载开销。当前每 root 启动一次 infer；多个 root 的转录依次进行。watcher 仅跟踪当前 root 的 pending 音频，用与翻译相同的 RJ 归组规则，一组全部判稳后入队。

替代方案“逐文件即时翻译”和“逐 RJ 重启 infer”曾讨论，但未纳入最终要求。作品分析需要尽可能齐全的样本，现状按作品就绪而不是每个文件立即分析。

### 完成语义从语言改为存在性

早期计划保留字幕语言识别，用户后续明确改成只看 A.lrc。为避免恢复日文字幕后被当完成，取消 Python 与 BAT 的 JA→LRC 占位恢复，翻译显式区分 source/target。

设计目标为：转录中间 LRC 尽快移至 JA，最终目标只在整轨成功后提交。**这不是强杀安全的完整事务协议**：原地 infer 输出到 A.lrc 与 watcher 归档之间仍存在时间窗，不得表述成“任意时刻强杀都无风险”。

### 原子提交译文，而非逐块写目标

临时文件在目标同目录，flush/fsync 后 os.replace。API 的 100 行块在内存合并；不把中日混合半成品写成最终 A.lrc，保持存在即跳过的语义。代价是当前音轨尚未提交的块会在中断时丢失。

### 上下文按作品显式传递

每组读取自身缓存；请求中传 terms、alias、worldview 和该轨 scriptbook_lines。没有把前一作品的完整消息历史追加到后一作品。track key 改为作品内相对路径，以区分 本編/01 与 特典/01。旧缓存的 stem／模糊回退还未完全消歧，不能宣称已彻底隔离。

### 启动入口最终统一

最初只改 Python，漏改生成 BAT：BAT 先恢复 JA 为 LRC、随后直接转录，分别造成“全跳过”和“配置无效／流式无交叠”。会话多次修复后，最终删掉 BAT 转录与归档，只保留启动入口。

五个启动文件名保留为兼容入口，但 CPU／低显存／高显存标题不再改变执行参数，实际以配置为准。这是用户排障的关键经验。

### extra_args 与硬件策略

extra_args 追加在固定 infer 参数之后、根目录之前。数组适合保留单个参数边界；字符串当前简单 split，不适合带空格的路径参数。

已有 auto_tune 默认开启，会按第一张 GPU 总显存选择精度和自动 batch 参数；显式 extra_args 不阻止前一步 compute_type 被覆盖。后续若要调整这一策略必须单独确认，不能继续凭本机显卡推断部署机器。

## 借鉴与未采用

保留集成版的成果及时落盘、作品级调度和不重复转录思想；不搬硬链接 exe、junction 临时资源、独立翻译 CMD、固定 D 盘临时目录和 Cline 专用代理。

精度／性能不能绝对化：FP16 和 INT8_FLOAT16 的速度、识别差异取决于模型、硬件和解码参数；显存占用不是吞吐指标；逐句日志也不证明没有 batching。历史会话中“FP16 必然更快／占用必然增大”等措辞不作为设计保证。

## 2026-11 追加设计

### NSFW 多级降级

单音轨内按整轨为单位逐级更换上下文，块级快速失败（任一 ≥5 行块 `parsed_count==0` 即终止本级）：
L1 完整上下文 → L2 和谐版世界观 → L3 仅和谐版角色表 → L4 纯文本。任一级成功即写目标；
L4 仍失败则跳过并计入 skipped，不再进入轮级重试队列（同上下文重翻价值低）。
和谐版 LLM 生成后落盘 `和谐版.worldview.json`，per-RJ 锁 + 双重检查防同组并发重复生成；
生成失败时跳过依赖它的级别，L4 仍可执行。开关关闭时仅 L1，失败回旧轮级重试。

### 独立上下文引擎与 Prompt

`_get_context_engine` 深拷贝全局配置，仅覆盖 `api.worldview`/`api.harmonize` 的
endpoint 字段与 `generation_params`；全空时复用翻译引擎，只配参数也建独立引擎。
`_load_rules_prompt` 分别读取世界观/和谐化的自定义规则文件；内置【输出要求】始终附加。
翻译基础 prompt 抽离为 `prompts/translation.txt`（源码/发布目录/exe 内嵌三处同源），
台本优先级与字幕标点改为代码常量，不受自定义文件替换。

### 请求诊断

三种协议在请求体裁剪完成后采集白名单设置；单次 chat 的本地列表随响应/异常传递，
不使用共享 `_last_*` 字段。OpenAI 记录粒度为 SDK 调用（可能含 SDK 内部重试），
Anthropic/Responses 为 HTTP 请求；时间带时区，响应模型缺失时标注未知。
不记录 headers、messages、URL 或任意扩展配置，避免凭证与提示词泄露。

### parsed_count 局部化

解析计数与译文一起作为单次调用返回值传递，不再写引擎实例字段；内部空结果重试
采用哪次响应就同步采用哪次计数。对外 `parse_json_translation` 保持双返回值兼容。

### extra_args 引擎配置键分流

`--vad_filter`/`--no_speech_threshold`/`--beam_size` 不在 infer CLI 中，但引擎会从
`generation_config.json5` 读取。HDEG 在启动前把它们从 argv 拆出，与引擎同目录配置
（容忍注释/尾逗号）合并到临时文件，通过 `--generation_config` 传入；转录结束删除。
解析失败明确报错并跳过本次转录。命令行参数仍优先于配置文件。
`smart=true` 依赖 `vad_filter=true`；该组合会同时启用外层分块与块内过滤（双层 VAD）。

### 发布交付

打包仍输出 `temp/`；曾将产物复制到根目录 `release/` 并尝试 Git LFS，
但 GitHub 拒绝公开 fork 上传新 LFS 对象，且 exe 超过普通 Git 100 MiB 上限，已回滚。
仓库不保存 exe；需要时本地 `build_release.py` 生成。打包脚本仅在 `extra_args` 缺省时
写入默认值，显式配置保留；发布目录被占用时清空复用，不中断打包。

# 001 验证证据与已知缺口

## 证据口径

- 本轮：只读核实源码、配置及 Git 日志；没有跑 pytest、构建、GPU 转录或真实 API。
- 历史会话：曾记录离线测试 54 项通过、多个约 117–119MB 发布产物构建成功；早期曾在 frozen exe 用 --run-script 检查 g2p。
- 这些历史结果不能证明最新发布入口、多根交叠、所有中断时序和真实最小化窗口均已验收。
- 路径行号以基线 cacf5e7c 的当前工作区为准，后续修改后请按符号定位。

## 已确认的实现

| 实现 | 位置 |
| --- | --- |
| BAT 只交给主引擎，不再自己转录 | build_release.py:331–401 |
| 无 A.lrc、有 A.ja.lrc 收集为待翻译 | pipeline/orchestrator.py:951–976 |
| 有 LRC／JA 的音频不进入本地 pending | pipeline/orchestrator.py:2647–2664 |
| extra_args 拼入 infer argv | pipeline/orchestrator.py:2694–2713 |
| 请求按任务显式携带作品上下文 | pipeline/orchestrator.py:2948–3031；engines/translate_engine.py:1078–1090 |
| 多根共用翻译池，转录依次跑根 | pipeline/orchestrator.py:3041–3134 |
| 相对路径轨键及 ASR 索引 | pipeline/orchestrator.py:847–863、1348–1366 |
| 原子写、LRC 转 SRT/VTT | io_adapter/lrc_handler.py：_atomic_write_lines、write_srt_from_lrc、write_vtt_from_lrc |

## 待修复／待确认（本轮不动代码）

### 数据与续跑边界

1. **pending 不等于引擎输入清单**：infer 接收整个 work_dir（orchestrator:2694–2713），是否跳过已有 JA 或汉化音频由外部版本决定。本地过滤不能保证引擎不重转。
2. **存在性规则与中间文件有窗口**：infer 先写 A.lrc，watcher 再归档；强杀发生在两者之间仍可能留下日文／截断 A.lrc，下次被用户确认的存在性规则跳过。copy2+unlink 本身也不是事务。不能声称“任意强杀都无损”。
3. **判稳不是完成证据**：watcher:79–115 仅看大小和 mtime，未验证句柄关闭、可解析内容或引擎完成信号；长写入停顿可能误判。finish:145–177 在失败后也强制收尾，可能归档不完整结果。
4. **归档返回 false 被忽略**：watcher:108–115、170–175 仍把音轨计入 settled，可能提前通知组就绪；旧 mtime 文件也计为 settled，数量不等于成功归档数。

### 调度、分组与重试

5. **多根入口绕过单根保护**：orchestrator:3185–3202 的提前返回发生在根检查、printer 初始化、无 Key 仅转录分支之前；非流式多根只返回最后一根统计。
6. **分组边界可能越界／提前就绪**：RJ 查找不限输入根，每个 watcher 只认识当前 root 的 pending；两个输入目录若归同一个 RJ 祖先，第一次回调不代表整部作品全局就绪。作品分析会递归祖先，可能越过输入范围。
7. **初始 pending 扫描存在竞态**：转录线程先启动，调度再扫描初始 pending；结合每作品只允许一次 dispatched，需测试早派发后新增 JA 是否漏译。
8. **失败重试丢 source_path**：translate_one_lrc 内部入队（orchestrator:2354–2363、2431–2440）未存源路径；流式重试从 task 取源（:2988），可能改读不存在的目标。首轮源／目标正确并不代表重试正确。
9. **重试收尾不完整**：流式 :3140–3151 未接旧路径最终失败标记和统计；translation_parallel=0 首轮钳制为1，重试却可能创建0 worker池。
10. **同名隔离仍有旧回退**：相对键存在，但 :3019 的 stem 回退与 :1245–1330 的包含／前四字符缓存匹配还可能误配。共享翻译引擎的 _last_parsed_count 等实例字段也有并发覆盖风险（translate_engine:178–180、965、1004、1144）。

### 功能接线和统计

11. **进度窗只开关、不写进度**：work_windows 提供 write，但 _process_work_stream 只调用 open/close（orchestrator:3009、3038）。标题文件不是实时音轨进度；重试也不再开窗。
12. **流式日志丢弃**：_translate_task_stream 的 stop_track_buffer 返回值未保留，没有 _record_track_run 或失败 dump（:2967–3000）。作品日志、manifest、完整总览未接原流程（对照旧路径 :3657–3669、4054–4130）。
13. **跳过统计／汉化过滤有 bug**：_collect_translation_pairs 多次扫描累加；:949、971–974 把 target→audio 字典反着解包，检查的可能是音频存在性。:943–960 的汉化音频先过滤、后绑定又无条件检查，使开关与实际翻译过滤失配。
14. **视频同步开关不统一**：_maybe_generate_video_subs 只补缺失；已有 SRT/VTT 仍被旧同步函数单独处理，并非全按 LRC 重建，也不完全受开关控制。
15. **隐式硬件覆盖**：auto_tune 按第一张 GPU 总显存自动改 compute_type，显式 extra_args 不阻止；示例配置未声明这个默认开关。

## 当前 8 个新增测试覆盖

文件：tests/test_stream_workflow.py。

- 单音频静态 LRC 判稳后回调归档、通知一次组就绪。
- 旧文件不归档但 settled=1，force 收尾不等待。
- 正常原子写与 replace 失败保留旧文件、清临时文件。
- 相对轨键区分子目录，正确去 JA 后缀。
- SRT/VTT 基本内容和时间戳格式。

未覆盖多根、真实 infer、写入暂停、回调失败、判定矩阵、汉化、进度更新、日志／manifest、重试源路径和 GPU 覆盖。现有 integration 用已有 JA/mock LLM、并行1、不开窗口／预检，不能替代转录交叠 E2E；素材缺失时还可能 skip。

## 后续验收建议

- 用假 infer 明确控制 A/B 作品产物与完成时刻，断言 A 请求早于 B 完成；不要只观察混排日志。
- 参数测试捕获 argv，证明 BAT 不绕过配置，带空格参数边界正确。
- 覆盖 A.lrc／A.ja.lrc 三种存在性状态，不引入语言检测。
- 注入归档失败、API失败、replace失败和不同强杀时点，验证磁盘与重试语义。
- 检查多根重叠、共同RJ祖先、深层目录、同stem轨、作品上下文隔离。
- Windows 上验证最小化窗口有实际进度；检查成功／失败／重试的日志文件。
- 干净发布环境测试 frozen g2p、词典离线可用、BAT多目录以及真实 infer 参数。

以上是待后续批准的修复／验收方向，不构成本轮新增需求或自动执行授权。

---
name: hz-rj-translate
description: >-
  使用当前宿主模型翻译 HDEG prepare_only 模式已准备好的 RJ 日文字幕任务包，校验后写出中文 .lrc。用户要求翻译 RJ 目录或消费 .hdeg-translation 准备包时使用；不承担转录、世界观生成、术语生成、台本预处理或脱敏。
---

# hz-RJ-translate

本 Skill 只负责翻译 HDEG 已准备的数据。先读 [references/workflow.md](references/workflow.md)；本机路径见 [config.json](config.json)。

## 边界

- 以 `C:/Users/Administrator/IdeaProjects/HDEG/.venv/Scripts/python.exe -I` 调用 `C:/Users/Administrator/.gemini/config/skills/hz-rj-translate/scripts/hdeg_cli.py`。不得进入不可信作品目录执行解释器。
- 只接受 HDEG 生成的 `.hdeg-translation/manifest.json`。数据未准备、非 READY、版本不支持、摘要错误或源文件变化时报告需重新运行 HDEG，不自行识别台本、生成世界观、补术语、对齐、脱敏或调用 API。
- `original` 与 `normalized` 是 HDEG 预先生成的两套保真资料。开轨前固定选择，默认 original；不因失败、缺资料或内容拒绝切换。
- 仅翻译本次授权范围内 `scan` 返回的实际音轨键。共享世界观不代表可以翻译兄弟目录；同一音轨只有一个执行者。
- 完整读取每块提示及衔接资料，按证据纠正 ASR。台本对齐不是语义正确概率；证据不足就暂停报告，不为凑 JSON 杜撰。
- 严格返回本块唯一完整 `1..N` 整数 ID 和非空字符串译文。JSON 用文件工具写入会话临时目录，`--in` 只传绝对路径，不拼接内联 JSON。
- 不手写 `.lrc`、不修改 `.ja.lrc` 或准备包。仅 CLI 在整轨通过校验后发布目标；已有目标默认跳过，只有用户明确要求指定轨重译才用 `--force`。
- 格式错误最多修正两次；明确内容策略拒绝立即 `fail-track` 并停止，不换会话、模型、上下文或子代理重试。COMMITTED / FAILED_STOPPED 为终态。
- 多轨可在宿主确有同模型子代理时并发最多 2 个，否则顺序执行。子代理工具名依宿主，不擅自切换模型。

## 流程

`scan` → `open-track --context original|normalized` → 读取提示并翻译 → `submit-chunk` → 可选 `submit-polish` → `COMMITTED`。

不可继续时 `fail-track` → `FAILED_STOPPED`。不再提供任何 prepare/ensure/save 类命令；分块、台本、世界观、术语和润色开关均由 HDEG 准备时确定。需要调整时返回 HDEG 重新准备。

没有自动 resume；旧版会话不兼容时停止，不删除旧数据。完成后按实际返回汇总成功、已有目标跳过、未准备、失效和失败，附输出或日志路径，不把草稿当成成品。

# 001-ds Agent Skill 接入 harness-only 模型：可行性评估

> 建立：2026-11-18；状态：planning（仅评估，未改项目代码）

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [prd.md](prd.md) | 背景、目标、非目标、约束 |
| [design.md](design.md) | 现状链路、可行性结论、方案对比、skill 架构、风险 |
| [implement.md](implement.md) | 分阶段实施、验收、未来文件清单、待确认项 |
| [verification.md](verification.md) | 本次评估的验证边界与已核对事实 |

## 30 秒结论

可行。当前翻译链路中只有一次 HTTP 模型调用是非确定性的，其余（prompt 构建、字幕解析、台本对齐、上下文加载、回写）都能用项目现有函数离线复现。做一个 `SKILL.md + Python 助手脚本` 的 skill，让 harness 内的 agent 充当模型传输层，就能在不改 HDEG 代码的前提下沿用世界观、术语、台本和四级降级逻辑。

不推荐把 Antigravity 订阅包装成 HDEG 的 engine：条款禁止第三方封装个人订阅凭证，且实现复杂度高于收益。

## 适用范围

- 输入：RJ 作品目录下已存在的 `A.ja.lrc`
- 输出：同目录 `A.lrc`，保留时间戳，删除空歌词行，同步同名 SRT/VTT
- 不做：转录、无人值守调度、把订阅凭证导出给外部程序

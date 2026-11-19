# 001-6.1sol · HDEG 准备模式与纯翻译 Skill

> 创建：2026-11-18；最新改造：2026-11-19。状态：**review，HDEG 216 项 / Skill 56 项离线通过，真实 API/GPU/Agent 翻译待验收**。关联 [001](../001/README.md)，保留其他任务不变。

## 30 秒接手

职责分为两段：HDEG 在 `app.prepare_only=true` 时完成转录及全部翻译前数据准备；`hz-rj-translate` 只消费 `.hdeg-translation/manifest.json`，使用宿主模型翻译、校验并写出 `A.lrc`。

开关默认 false，关闭时原 HDEG 工作流不变。新模式输出 original 与 normalized 两套保真资料；规范整理不改变年龄、身份、人物关系、同意状态或固定译名，不使用旧四级降级的隐去事实规则。Skill 不再生成台本、世界观、术语、读音对齐或脱敏，没有四级重试。

Skill 路径：`C:/Users/Administrator/.gemini/config/skills/hz-rj-translate/`，界面名 `hz-RJ-translate`。旧 `hdeg-translate` 保持不变。本轮在用户批准后改动 HDEG 准备入口和默认配置；没有更改共享 API 客户端或原翻译引擎行为。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [prd.md](prd.md) | 本轮需求、范围与验收 |
| [design.md](design.md) | 生产／消费契约、数据流与失败边界 |
| [implement.md](implement.md) | 实际实现、验证命令与结果 |
| [task.json](task.json) | 任务元数据 |

## 记录方式

仓库采用文档式 Trellis，无 get_context/task CLI、spec 或运行时活跃任务机制；本轮不初始化或伪造这些能力。任务档案不是提交或完整验收声明。

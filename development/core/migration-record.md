# AIFPatent 迁移完成记录

> 状态：历史记录，迁移已完成
>
> 日期：2026-07-20

## 迁移结果

AIFPatent 从原 AI4Patent 工作树建立独立仓库，并完成以下迁移：

1. 建立不包含旧 `.git`、运行数据、缓存、日志和凭证的源码基线；
2. 删除 OpenCode Runtime、旧通用任务 API、下载逻辑和认证文件回退；
3. 将固定 11 步 IDEA 编排迁移到 LangGraph `StateGraph`；
4. 使用 LangChain `ChatOpenAI` 统一 OpenAI-compatible 结构化模型调用；
5. 保留业务 SQLite、RunStore、Evidence、Provider、审计和 Manifest 为权威领域层；
6. 保持 FastAPI、SSE、Case/Run 和前端工作区；
7. 完成图并发、取消、BYOK 不落盘和真实 E2E 回归；
8. 推送独立 GitHub 仓库并验证原 AI4Patent 工作树未被修改。

## 里程碑

| 里程碑 | 提交 | 结果 |
|---|---|---|
| AIF-BOOT-001 | `ae2331c` | 独立 AIFPatent 基线 |
| AIF-OC-001 | `0a32493` | 删除 OpenCode 运行依赖 |
| AIF-GRAPH-001 | `20587d9` | LangGraph/LangChain 核心迁移 |
| AIF-STABILITY-001 | `5e44e1f` | 证据绑定、并发、取消和真实 E2E 稳定性 |

Git 历史是提交内容的最终权威来源，本文件不用于描述当前运行行为。

## 原项目隔离证据

- 原 AI4Patent HEAD：`8a2795819cbed647c36e5d2cde49eb6730627228`；
- 原工作树状态哈希：`5bdb5399664ed99835633e56cab71e8066684248b9787787d104f2b7f804be04`；
- 原源码内容哈希：`9ee3381d78e25706142054e4b9ffc87cbfceb366d8f2f5a8ab51b04780f6bb4e`。

迁移前后三项一致。

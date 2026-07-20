# AIFPatent 架构说明

## 1. 一句话理解

AIFPatent 是一个“固定流程的专利评估系统”，不是让大模型自由行动的聊天 Agent。LangGraph 决定 11 个步骤何时执行和如何恢复，LangChain 统一模型调用，项目自己的领域服务负责检索、证据、判断、审计和报告。

## 2. 分层

```text
浏览器 / tools CLI
        |
FastAPI + SSE + Case/Run API
        |
LangGraphWorkflow（固定 11 节点、重试、超时、取消、checkpoint）
        |
WorkflowExecutor（每个节点的确定性业务入口）
        |
领域服务：Agent / Retrieval / Documents / Novelty / Inventiveness / Value / Audit / Report
        |
Provider：Google Patents / EXA MCP / LangChain ChatOpenAI
        |
业务 SQLite + RunStore        LangGraph SQLite
```

业务 SQLite 和 RunStore 是产品事实来源；LangGraph SQLite 只是执行游标。两者不能互相替代。

## 3. 固定图

```text
START
  -> PREPARE_INPUT
  -> PARSE_IDEA
  -> VALIDATE_IDEA_MODEL
  -> PLAN_QUERIES
  -> RETRIEVE_CANDIDATES
  -> NORMALIZE_AND_FETCH
  -> ANALYZE_DOCUMENTS
  -> DETERMINE_NOVELTY
  -> ANALYZE_INVENTIVENESS
  -> ASSESS_VALUE
  -> AUDIT_AND_REPORT
  -> END
```

每个节点只返回轻量 Graph State：`run_id`、最后完成节点、完成节点数。完整 IDEA、检索命中、全文、证据映射、结论和报告不放进 Graph State，避免重复存储、密钥污染和 checkpoint 膨胀。

## 4. 两套持久状态为什么都要保留

- LangGraph Checkpointer：保存某个 `run_id/thread_id` 执行到哪个节点，为节点边界恢复提供基础。
- 业务 SQLite：保存 Case、不可变 Run 输入、步骤 attempt、Provider Call、Tool Call、文献、Evidence、审计和终态。
- RunStore：保存输入快照、`report.json`、`report.md` 和带哈希的 Manifest。

节点重放时先检查业务 checkpoint 和写一次表；已经成功写入的领域结果直接复用。因此即使进程在“业务写入成功、LangGraph checkpoint 尚未落盘”的窗口退出，也不会重复制造证据或报告。

## 5. 重试、取消与重启

- LangGraph 对失败节点最多重试配置的次数；每一次都同步记录为业务 `run_steps.attempt`。
- 节点超时由执行器包裹，超时也进入相同的 attempt 和重试语义。
- 用户取消会取消 asyncio Task，把运行中步骤标记为中断，并将 Run 置为 `CANCELLED`。
- API Key 是 BYOK 临时凭证，绝不持久化。因此服务重启后，原 Run 保留 checkpoint 和历史证据，但会以 `RUNTIME_API_KEY_REQUIRED_AFTER_RESTART` 失败；用户重新输入凭证后创建新 Run，而不是偷偷恢复并复用旧密钥。

## 6. LangChain 模型适配

`StructuredModelClient` 使用 LangChain `ChatOpenAI` 调用 OpenAI-compatible Chat Completions，同时保留项目原有的二次约束：

- Agent 对应的 Pydantic Schema 校验；
- JSON 解析失败后的带错误摘要纠正；
- 面向用户判断文字的简体中文门禁；
- Evidence ID、公开号和确定性完成门禁；
- D2 公开号与同文献 Evidence 的精确绑定，以及一次带权威合法清单的内部纠错；
- 火山方舟网关的 `thinking: disabled`；
- 代理连接失败后的直连回退；
- Token 用量和响应 ID 写入脱敏 Tool Call 审计。

没有采用自由 `create_agent` 循环，因为专利评估需要可复核的固定顺序，而不是让模型自行决定是否检索、是否审计或何时结束。

## 7. 凭证边界

Base URL、API Key、Model 每次从网页或 CLI 提交。API Key 只进入当前后台 Task 的 `ContextVar`，不进入：

- Graph State 或 LangGraph SQLite；
- `config/ai4patent.json`；
- Case/Run 数据库；
- JSONL 调试日志和 Tool Call 请求摘要；
- 浏览器 localStorage/sessionStorage；
- Git 仓库。

## 8. 可观测性

系统同时保留三类观察面：

- `/api/idea/runs/{run_id}`：产品状态和 11 步进度；
- `/api/idea/runs/{run_id}/debug`：attempt、Tool Call 和脱敏 JSONL 事件；
- `/api/system/health`：业务数据库、LangGraph Checkpointer、缓存、模型配置、EXA、Google Patents 与恢复器状态。

前端调试时间线直接显示 LangGraph 图和节点事件，不依赖 LangSmith 才能排障。未来可以把 LangSmith 作为可选遥测，而不能把它变成系统运行前提。

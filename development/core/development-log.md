# AIFPatent 核心追加式开发日志

> 本文件只记录 AIFPatent 独立仓库建立后的核心系统工作。
>
> 当前产品行为以代码、测试和 `../../docs/aifpatent-architecture.md` 为准。

## 2026-07-20 — AIF-BOOT-001

- 从 AI4Patent 当前工作树建立独立 AIFPatent 仓库；
- 未复制旧 Git 历史、虚拟环境、运行数据库、Case/Run、缓存、日志或凭证；
- 保留专利领域服务、SQLite、Evidence、审计和 Manifest 作为迁移基础。

## 2026-07-20 — AIF-OC-001

- 删除 OpenCode Runtime、旧通用 API、下载逻辑和认证文件回退；
- CLI 迁为 `tools/idea_workflow.py`，只调用权威 HTTP API；
- 网页和 CLI 的 API Key 继续只存在于临时运行内存。

## 2026-07-20 — AIF-GRAPH-001

- 将 IDEA 编排迁移为固定 11 节点 LangGraph；
- `run_id` 作为 `thread_id`，Graph State 只保存轻量执行状态；
- LangChain `ChatOpenAI` 接管 OpenAI-compatible 结构化模型传输；
- 业务 SQLite、RunStore、Evidence、审计和报告仍是权威事实来源；
- 完整 181 项离线测试和 BYOK 不落盘检查通过。

## 2026-07-20 — AIF-STABILITY-001

- 增加 D2 公开号与同文献 Evidence 的定向纠错和严格绑定；
- 验证两个 Run 共享 CompiledStateGraph 的独立 Checkpoint；
- 补强节点取消、业务 attempt 和 Run 终态一致性；
- 真实 quick Run 完成 11/11 节点、10 篇深读和 Manifest 校验；
- 完整 184 项离线测试及真实模型冒烟通过。

## 2026-07-20 — AIF-DELIVERY-001

- 独立仓库：`BeingOK3/AIFPatent`；
- `main` 保存孵化基线，`feat/langgraph-migration` 保存迁移提交链；
- 原 AI4Patent HEAD、工作树状态和源码哈希在迁移前后保持一致。

## 2026-07-20 — AIF-GPAT-002

- Google Patents 按 origin 在同一事件循环内全局串行化；
- 锁覆盖等待、HTTP 请求、响应读取和风控判断；
- 搜索间隔 8–12 秒，详情请求间隔 3–5 秒；
- 连接/传输错误、普通 5xx、429 和 Google `Sorry` 页面采用不同重试/冷却策略；
- `provider_circuit_breakers` 作为 SQLite Schema v3 的持久熔断状态；
- 目标 Provider、数据库、配置和合同回归通过；完整套件仍存在与本修改无关的既有 TestClient/取消等待卡住路径。

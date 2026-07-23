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

## 2026-07-23 — AIF-IDEA-SERP-DESIGN-003

- 类型：IDEA SerpAPI 候选检索与全文回退增量设计。
- 根因：当前默认关闭 Google 直连和匿名 Exa，但 IDEA Runtime 尚未装配已经存在的 SerpAPI Provider，导致 Provider 列表为空并停止为 `PROVIDERS_UNAVAILABLE`。
- 决策：SerpAPI 成为 IDEA 默认主检索与首选详情源；Google/Exa 保留为显式启用的补充源。
- 调度：同一 Provider 在 Run 内串行；SerpAPI 鉴权/额度错误与 Exa 429 首次出现后快速熔断；Tool Call/Debug 继续记录脱敏状态。
- 边界：不修改 IDEA 固定 11 步、模型 BYOK、证据门禁、Corpus/RAG 或追问行为。
- 文档：详见 `idea-serpapi-design.md`；实现与容器验收作为下一工作单元。

## 2026-07-23 — AIF-IDEA-SERP-CORE-004

- 类型：IDEA SerpAPI 候选检索、详情回退和运行调度接入。
- Runtime：默认装配 `serpapi_google_patents` 并使用现有 Cache、本地 JSON Secret 和配置超时；当前默认关闭的 Google/Exa 不再导致 IDEA Provider 列表为空。
- 调度：每个 Run 内同一 Provider 串行，不同 Provider 保持并行；SerpAPI 凭证/鉴权/额度错误和 Exa 429 首次发生后快速熔断，后续调用记录 `DISABLED`。
- 详情：同等健康状态下依次尝试 SerpAPI、Google、Exa；既有全文身份、Evidence、Corpus 和报告门禁保持不变。
- 真实验收：容器 IDEA Runtime 仅装配 SerpAPI；受控搜索返回 10 条，`US10587019B2` 详情成功，摘要 310 字符、权利要求 4,804 字符。
- 测试：84 项 Runtime/Retrieval/Search Strategy/Provider/Health/Config/Debug 相关测试通过；Python 编译、Node 语法和 Diff 检查通过。既有 `test_execution` 长等待超过 90 秒后终止，不计为通过。
- Git：本实现作为第 4 个核心工作单元，将与第 3 个设计提交一起推送远程 `develop`。

## 2026-07-23 — AIF-CLAIM-EVIDENCE-DESIGN-005

- 类型：IDEA 首次报告缺失独立权利要求证据的跨机器故障设计。
- 根因：全文抓取把“存在任一正文”视为成功，Chunker 只识别 claims 正文行首编号且忽略结构化 claim spans；失败直到 Corpus 冻结后的报告门禁才暴露，因此同一 Version 重试得到相同结果。
- 决策：保留强制独立权利要求门禁；把证据完整性检查前移到 Provider fallback，失败专利使用同轮合格候补补位；Chunker 升级并按 Version 选择最新完整批次。
- 可观测性：新增稳定缺失证据错误码；最终兜底错误补充公开号、claims Chunk 数和 Chunker 版本，不记录 claims 全文或凭据。
- 文档：详见 `independent-claim-evidence-repair.md`；实现、回归和容器验收作为下一工作单元。

## 2026-07-23 — AIF-CLAIM-EVIDENCE-CORE-006

- 类型：IDEA 独立权利要求证据完整性、回退和 Chunk 版本修复。
- 抓取门禁：IDEA 在接受 Provider 详情前验证摘要和可识别独立权利要求；缺失时记录 `ABSTRACT_MISSING`/`INDEPENDENT_CLAIM_MISSING` 合同错误并继续 Provider fallback，不再把不完整文档冻结进新 Run。
- 候补：首选深读文档不合格时，从同轮已筛选、日期合格、相关性达标且公开号有效的剩余候选中确定性补位；不新增搜索、不降低阈值，并记录 `DEEP_REVIEW_BACKFILLED` limitation。
- Chunk：`claims-paragraphs-v2` 在正文编号之外验证并使用 Provider claim spans；非法边界不生成独立权利要求。PostgreSQL Chunk、词法和向量读取按每个 Version 最近完整写入的 Chunker 批次选择，v1 保留审计但不与 v2 混入上下文。
- 可观测性：最终报告门禁仍 fail closed，并额外输出公开号、claims Chunk 数和 Chunker 版本；README 明确旧失败 Run 必须新建 Run，不能靠重试改写冻结证据。
- 验证：claims/抓取/候补/报告/词法/向量/PostgreSQL/SerpAPI 聚焦测试 62 项通过；相关 Corpus、Context、Report、Runtime 和 E2E 组合回归共 129 项通过、3 项按环境跳过。完整 discover 再次停在既有 `test_execution` 长等待路径并已终止，未计为通过。
- 真实运行态：PostgreSQL 回滚事务验证 v1/v2 共存时仅选 v2；应用镜像重建成功，app/PostgreSQL/Redis/MinIO 全部 healthy；容器内冒烟返回 `claims-paragraphs-v2 True`，HTTP OpenAPI 可访问。

## 2026-07-23 — AIF-ARCHITECTURE-DIAGRAM-007

- 类型：当前全系统架构可视化文档。
- 图示：新增可编辑 Mermaid 总体架构图和双业务数据流图，覆盖 Web/API、IDEA 固定 11 步、Landscape 固定 8 步、模型与 SerpAPI Provider、首次报告/追问 RAG、SQLite/PostgreSQL/pgvector/MinIO/Redis/RunStore 以及容器边界。
- 边界：明确模型只执行受 Schema 约束的语义任务，SerpAPI 是当前默认专利源，IDEA 初筛与全文证据门、Landscape 唯一全集统计与精读聚类使用不同集合。
- 索引：更新 `docs/README.md`，将 follow-up RAG 从“尚未实现”修正为设计、实现与验收历史。
- 涉及文件：`docs/aifpatent-architecture-diagram.md`、`docs/README.md` 和本日志。

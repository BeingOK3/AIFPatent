# AIFPatent 当前系统架构

> 状态：当前实现说明
>
> 更新日期：2026-07-23
>
> 适用范围：当前已上线的 IDEA 评审、首次报告/追问 RAG、专利态势分析和单机容器部署
>
> 配套图：`aifpatent-architecture-diagram.md`

## 1. 系统定位

AIFPatent 是固定流程、证据约束的专利分析系统，当前包含 IDEA 评审与报告内追问、专利态势分析两条独立业务链，不是让大模型自由决定是否检索、分析或结束的聊天 Agent。

职责边界：

```text
LangGraph       决定固定步骤、顺序、重试、超时和恢复边界
领域服务        执行检索、归一化、证据、判断、审计和报告
受限模型 Agent  执行 IDEA 解析、查询规划和有 Schema 的语义分析
确定性程序      执行日期、编号、去重、矩阵、哈希和完成门禁
SQLite/RunStore 保存 Run、审计、不可变输入和报告
PostgreSQL/MinIO 保存 Corpus、Chunk、Context、Citation 与耐久正文
Redis           提供任务队列、租约和跨进程协调
```

## 2. 运行时分层

```text
IDEA 页面 / Landscape 页面 / tools/idea_workflow.py
              │
              ▼
FastAPI + Case/Run/Thread API + SSE
              │
   ┌──────────┴──────────┐
   ▼                     ▼
IDEA LangGraph       Landscape Workflow
固定 11 节点          固定 8 节点
              │
   ┌──────────┼──────────────────────────────┐
   ▼          ▼                              ▼
领域服务    受限模型 Agent                 Provider
检索/证据   LangChain ChatOpenAI            SerpAPI / Google Patents / EXA MCP
判断/审计   Pydantic 结构化输出
   └──────────┼──────────────────────────────┘
              ▼
业务 SQLite + RunStore + FIFO Cache
PostgreSQL/pgvector + MinIO + Redis
LangGraph SQLite（只保存执行游标）
```

`backend/idea/runtime.py` 和 `backend/landscape/runtime.py` 分别装配两条业务链。IDEA SQLite 保存 Run 与评审事实，Landscape 使用独立 SQLite；PostgreSQL/MinIO 保存耐久 Corpus 与 RAG 事实；LangGraph SQLite 只保存轻量执行状态，不能替代业务记录。

## 3. 固定 11 步 Workflow

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

每个节点只向 Graph State 返回：

- `run_id`；
- 最后完成节点；
- 完成节点数。

完整 IDEA、检索命中、专利全文、Evidence、判断和报告不进入 Graph State，防止 Checkpoint 膨胀和密钥/正文污染。

## 4. 检索与全文获取

### 4.1 当前检索性质

外部专利候选召回属于“大模型辅助生成检索式的文本检索”，不是使用本地向量库替代 Google Patents：

1. 模型解析 IDEA 为技术领域、问题、效果和 F1..Fn；
2. Query Planner 生成中英文术语、同义词、上位词和可选 IPC/CPC；
3. SerpAPI Google Patents 接收文本 `q`，返回结构化专利字段；
4. 显式启用时，Google Patents 直连接收文本 `q`，EXA 接收文本查询并定向 `patents.google.com/patent`；
5. 本地根据标题和搜索摘要中的概念组覆盖率初筛；
6. 入选文献才进入模型全文语义分析。

外部召回后的深读全文会写入版本化 Corpus。首次报告与追问共用 PostgreSQL 词法检索、可选 pgvector、RRF、章节权重和多样性规则；默认未配置 Embedding 时明确运行在 `LEXICAL_ONLY`，不会伪装为向量混合召回。

### 4.2 Provider 调度和限流

检索服务会在每轮异步调度“查询 × Provider”调用。当前默认只启用 SerpAPI；Google Patents 直连和匿名 Exa 在具备网络或额度条件后可由配置重新启用：

- 同一个 Run 内同一 Provider 串行，不同 Provider 之间可以并行；
- SerpAPI 使用本地 JSON Secret、统一 Cache 和稳定错误码，鉴权/额度错误后对当前 Run 快速熔断；
- EXA 首次出现 429 后对当前 Run 快速熔断；
- Google Patents 所有 Run 和 Provider 实例共享同源请求门；
- Google 请求锁覆盖等待、HTTP 请求、响应读取和风控判断；
- Google 搜索间隔随机 8–12 秒；
- Google 详情间隔随机 3–5 秒；
- Google 域名实际网络并发固定为 1。

失败分类：

- 连接、DNS、传输或超时：有界重试和退避；
- 普通 5xx：最多重试一次；
- 429：遵守 `Retry-After`，缺失时至少冷却 15 分钟；
- Google `Sorry`/异常流量页面：不立即重试，冷却 30–60 分钟；
- 解析或契约错误：不重复下载。

熔断状态保存在业务 SQLite 的 `provider_circuit_breakers`，跨 Run 和服务重启生效；缓存命中不受熔断阻止。

### 4.3 合并、筛选和停止

命中按以下身份确定性合并：

1. 标准化公开号；
2. 标准化申请号；
3. 已知同族 ID；
4. URL。

标题、优先权日和申请人只生成疑似同族标记，不自动合并。系统按评估日、概念组覆盖和标题命中分数选择深读候选。

停止原因包括：

- `SATURATED`；
- `CANDIDATE_MAX`；
- `QUERY_EXHAUSTED`；
- `PROVIDERS_UNAVAILABLE`。

### 4.4 全文 Provider

入选公开号使用有界并发抓取；优先选择本轮健康 Provider，同等条件下依次使用 SerpAPI、Google Patents、EXA，失败后继续回退。SerpAPI 详情引擎提供结构化摘要、权利要求和同族字段；Google 全局请求门仍会把实际 Google 网络请求串行化。

解析后的 `FetchedDocument` 包含元数据、摘要、权利要求、说明书及章节 span。IDEA 还要求摘要和可识别的独立权利要求；不完整响应会触发 Provider 回退和同轮候选补位，不能冻结为合格深读证据。

## 5. 专利信息处理和结论

### 5.1 Evidence Packet

每篇深读文献构建最多约 40,000 字符的证据包：

- 摘要和权利要求优先；
- 说明书按 IDEA 特征词项覆盖率排序；
- 默认最多选择 12 个说明书 span；
- 每个片段生成稳定 Evidence ID 和 SHA-256。

### 5.2 文献级语义分析

Document Analyzer 对单篇专利逐项映射 F1..Fn：

- `DISCLOSED`；
- `PARTIAL`；
- `NOT_DISCLOSED`；
- `UNCERTAIN`。

每个 `DISCLOSED/PARTIAL` 必须引用当前文献 Evidence。后端验证特征全集、公开号、Evidence 白名单和跨文献边界。

### 5.3 新颖性

新颖性由确定性程序从已审计矩阵生成：

- 单篇文献披露所有必要特征：`NOT_NOVEL`；
- 每篇均存在明确缺口且深读达到门槛：`NOVEL`；
- 映射不确定或深读不足：`UNCERTAIN`。

系统禁止跨文献拼接破坏新颖性，并拒绝公开日晚于评估日的文献进入矩阵。

### 5.4 创造性、价值和审计

- 创造性以最多三条 D1 路线分析区别特征；D2 必须来自已深读文献并绑定自身 Evidence；
- 当前创造性阶段不会自动围绕区别特征发起新外部检索；
- 价值分析只使用冻结的 IDEA、新颖性和创造性依据，评分为 1–5；
- 审计先执行确定性完整性检查，再执行受限语义复核；
- 报告模型只能组织已冻结事实，不能发明公开号、日期或统计。

## 6. 数据与文件生命周期

### 6.1 业务 SQLite（Schema v3）

保存：

- Case、Run、不可变输入和配置快照；
- 步骤 attempt 和 write-once stage result；
- 查询、命中、Provider/Tool Call；
- 专利元数据、Run—文献关系；
- IDEA 特征、Evidence、特征映射；
- 新颖性、创造性、价值和审计；
- 报告索引、缓存索引和 Provider 熔断状态。

### 6.2 RunStore

`workspace/idea-runs/` 保存：

- 输入快照；
- 附件快照；
- `report.json`；
- `report.md`；
- 带文件大小和 SHA-256 的 Manifest。

Run 历史不自动删除；重新运行创建带 `parent_run_id` 的新 Run。

### 6.3 LangGraph Checkpoint

`data/langgraph/checkpoints.db` 只保存图执行游标和轻量 State。业务步骤是否完成以业务 SQLite 和完成门禁为准。

### 6.4 FIFO Cache

`workspace/cache/` 是最多 1 GiB 的可重建 FIFO Cache：

- 读取不会续期；
- 清理不会触碰 RunStore；
- Provider 搜索和全文响应可以复用；
- 它不是耐久专利数据库。

FIFO Cache 只负责 Provider 搜索和详情响应复用，不是耐久语料事实来源。

### 6.5 PostgreSQL Corpus、MinIO 与 RAG

启用默认特性时，深读文献先创建不可变 Corpus Version、结构化 Chunk 和 Run—Version 冻结关系，再进行首次报告分析：

- PostgreSQL 保存专利身份、Version、Chunk、词法/向量索引、Retrieval Hit、Context Manifest、Thread/Turn 和 Citation；
- MinIO/S3 保存按内容寻址的规范化全文 Blob；
- 首次报告与追问都必须传入明确的 Version allowlist 和 `corpus_snapshot_hash`；
- Context 与 Citation 可回查到真实 Chunk，模型不能扩大文献范围；
- Embedding 配置独立于网页聊天 BYOK，关闭时合法降级为词法召回。

### 6.6 Landscape 独立存储

`data/aifpatent/landscape.db`、`data/langgraph/landscape-checkpoints.db` 和 `workspace/landscape-runs/` 保存专利态势的输入、查询、候选、排序审计、精读、聚类和报告，不写入 IDEA 业务表。

## 7. 重试、取消和重启

- LangGraph 对节点异常执行有界重试；
- Harness 为每次尝试持久记录 `run_steps.attempt`；
- 节点超时进入相同失败和重试语义；
- 取消会取消 asyncio Task、标记运行中步骤中断并将 Run 置为 `CANCELLED`；
- 已成功的领域结果通过业务 Checkpoint 复用；
- 服务重启后临时 API Key 不可恢复，未完成 Run 进入 `FAILED / RUNTIME_API_KEY_REQUIRED_AFTER_RESTART`。

## 8. 模型与凭证边界

`StructuredModelClient` 使用 LangChain `ChatOpenAI` 调用用户提供的 OpenAI-compatible API，并保留：

- Pydantic Schema；
- JSON 解析纠错；
- 简体中文输出门禁；
- Evidence ID 和公开号白名单；
- D2—Evidence 精确绑定纠错；
- 火山方舟 `thinking: disabled`；
- 代理失败后的直连回退；
- 脱敏 Token/响应 ID 审计。

Base URL、API Key 和 Model 每次从网页或 CLI 提交。API Key 只进入当前后台 Task 的 `ContextVar`，不进入：

- Graph State 或 LangGraph SQLite；
- 配置文件；
- 业务数据库；
- JSONL 调试日志；
- 浏览器持久存储；
- Git 仓库。

## 9. 终态与完成门禁

- `COMPLETED`：11 步成功、无 critical audit、Manifest 校验通过且没有限制；
- `COMPLETED_WITH_LIMITATIONS`：报告和门禁有效，但存在 Provider 降级、深读不足等明确限制；
- `FAILED`：重试耗尽、无全文、关键证据/日期/引用不合法或完成门禁失败；
- `CANCELLED`：用户取消，保留已产生的过程记录。

模型自称“完成”不能改变 Run 终态。

## 10. 可观测性

- `/api/idea/runs/{run_id}`：产品状态和 11 步进度；
- `/api/idea/runs/{run_id}/debug`：attempt、Tool Call 和脱敏事件；
- `/api/idea/runs/{run_id}/events`：SSE 产品进度；
- `/api/idea/runs/{run_id}/followups/*`：报告内 Thread、Turn、SSE 和 Citation；
- `/api/landscape/runs/{run_id}`：专利态势状态和 8 步进度；
- `/api/landscape/runs/{run_id}/debug`：双语检索式、友商别名、Provider、过滤和候选排序审计；
- `/api/system/health`：数据库、Checkpointer、缓存、模型配置、Provider 和恢复器状态；
- `/api/system/cache`：FIFO 容量和清理状态。

前端直接显示 Workflow 和 Tool Call，不依赖 LangSmith 才能运行或排障。

## 11. 当前明确限制

1. 外部候选召回不是项目自建向量语义检索；
2. IDEA 候选初筛主要使用标题与搜索摘要的词项覆盖，只有入选后才核验权利要求全文；
3. SerpAPI 受账户额度与速率限制，需监控用量并复用缓存；
4. Google Patents 直连和 EXA 补充路径分别可能受网络出口、风控和匿名额度影响；
5. 默认 Embedding 关闭，当前首次报告与追问实际为 `LEXICAL_ONLY`；
6. 当前没有法律状态、有效权利要求和审查档案的权威联动；
7. 创造性 D2 只来自首次深读集合，不自动二次检索；
8. Landscape 全族状态只反映 Provider 返回的已确认成员，不能证明全球同族完整；
9. 当前评估是技术与现有技术初步分析，不是正式法律意见；
10. 当前是公开共享、单实例部署，不包含账号、租户、ACL 或公网生产安全基线。

## 12. 文档边界

- 本文件描述当前已经实现的系统；
- 根 `README.md` 描述安装、使用和运行接口；
- `development/core/` 保存核心系统历史记录，不作为当前行为规范；
- `development/followup-rag/` 保存 Corpus、首次报告/追问 RAG 的设计、实现历史和后续方向；
- `development/landscape/` 保存专利态势重构的产品需求、分类标准和开发规格；
- 代码、配置 Schema 和测试在发生冲突时是当前实现的最终依据。

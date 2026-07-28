# 源码实态审计

审计日期：`2026-07-27`

## 1. 结论

当前项目是一个完成度较高的“专利证据流水线”，不是严格意义上的自主 Agent。

它已经具备适合作为 Agent 底座的难点能力：结构化模型输出、专利多源检索、证据版本冻结、分块检索、引用校验、审计记录、报告生成和追问。但模型目前只在固定步骤中完成语义转换，不能根据观察自主选择下一步；工作流、恢复、数据权威和上下文预算也存在多套实现。

项目的求职价值不在“用了 LangGraph”，而在以下可验证能力：

- 把非结构化创意拆成专利特征并生成检索计划；
- 多提供商检索、抓取、归一化和候选筛选；
- 将证据保存为不可变版本，按运行范围限制引用；
- 把结论追溯到 `Evidence/Citation/Chunk/Version`；
- 对新颖性、创造性、价值和报告做结构化校验；
- 在追问中区分“历史上下文”和“可引用证据”。

最有价值的升级方向是“证据约束、可恢复、有预算的研究 Agent”，而不是堆叠更多人格化 Agent。

## 2. 审计范围与证据

本次读取并交叉核对：

- 后端实际入口、运行时依赖装配、IDEA/追问/Landscape 工作流；
- SQLite 与 PostgreSQL schema、S3 语料、LangGraph checkpoint；
- 检索提供商、混合检索、上下文组装、引用和报告链路；
- API、任务管理器、部署脚本和配置字段的实际使用；
- `backend/tests` 下 87 个测试文件（约 14,473 行）；
- 三个后端领域目录约 23,978 行 Python 源码。

既有项目文档没有被用作结论依据。

## 3. 实际技术栈

| 层 | 实际实现 |
|---|---|
| API | Python、FastAPI、Pydantic v2、SSE |
| 异步执行 | `asyncio.create_task` 进程内任务管理器 |
| 工作流 | LangGraph + SQLite checkpointer；业务步骤另由自建 Harness/Repository 记录 |
| 模型调用 | OpenAI-compatible HTTP 接口；JSON Schema 提示、解析、校验和重试 |
| 主业务存储 | IDEA/Landscape 分别使用 SQLite |
| RAG/追问存储 | PostgreSQL；可选 pgvector |
| 对象存储 | S3-compatible，按内容 hash 保存专利版本 |
| 检索 | SerpAPI Google Patents、Google Patents 直连、Exa MCP 适配器 |
| RAG | PostgreSQL lexical search + 可选向量 + RRF 融合 + section/diversity 调整 |
| 缓存 | 本地文件缓存；Redis 适配代码存在但未进入实际运行装配 |
| 前端 | 原生 HTML/CSS/JavaScript，IDEA 与 Landscape 两套页面逻辑 |
| 部署 | Docker/Compose、PostgreSQL、S3、Redis、单 Uvicorn worker |

默认配置中 embedding 关闭，因此当前默认混合检索实际上退化为 lexical-only。

## 4. 真实运行链路

### 4.1 启动与依赖装配

`backend/main.py` 在导入期读取配置并建立 IDEA、Landscape 运行时。IDEA 的真实装配集中在 `backend/idea/runtime.py`：

1. 初始化 SQLite `Database`、本地 `CacheStore`、`RunStore`。
2. 建立固定 `WorkflowHarness` 和 `StructuredModelClient`。
3. 按配置建立检索提供商。
4. 若开启语料/RAG，要求 PostgreSQL、S3 环境变量，并建立语料、引用、上下文和追问仓储。
5. embedding 开启时才建立 pgvector 索引和查询嵌入服务。
6. 建立 IDEA 11 步执行器及追问 7 步执行器。

因此 `start.sh --local` 并不天然等于“纯本地模式”：当前默认配置开启语料、首次评审 RAG 和追问 RAG，缺少 PostgreSQL/S3 环境变量时运行时会在构建阶段失败。

### 4.2 IDEA 首次分析

实际步骤固定为：

1. `PREPARE_INPUT`
2. `PARSE_IDEA`
3. `VALIDATE`
4. `PLAN_QUERIES`
5. `RETRIEVE_CANDIDATES`
6. `NORMALIZE_AND_FETCH`
7. `ANALYZE_DOCUMENTS`
8. `NOVELTY`
9. `INVENTIVENESS`
10. `VALUE_ANALYSIS`
11. `AUDIT_AND_REPORT`

`backend/idea/execution.py` 用大型条件分派逐步执行，没有条件边、动态计划或再规划。LangGraph 负责按序调用节点，自建 `WorkflowHarness` 同时记录步骤、attempt、顺序、hash 和运行状态。

检索按 LLM 生成的 round 分组；每轮对每个启用提供商执行查询，然后归并、覆盖率筛选、抓取和候补回填。Provider 内部有并发限制，但应用任务仍在单进程内运行。

抓取后的文档一方面进入 SQLite 当前文档表，另一方面在 RAG 模式下写入 S3/PostgreSQL 不可变语料、chunk 和 run scope。文档分析建立本轮上下文，将临时 `C1..Cn` 映射到持久 Evidence ID，验证引用后清空 SQLite 中大段正文。

### 4.3 追问

追问是固定七步：

1. 冻结范围；
2. 分类与制定一次计划；
3. 检索；
4. 组装上下文；
5. 生成回答；
6. 校验回答；
7. 持久化响应。

模型能设置 `requires_new_research`，但没有真正的“创建子研究任务—观察结果—重新规划”闭环。中间对象保存在 `FollowupBusinessHandler._memory` 的进程内字典中。重启时 `FollowupTaskManager.resume_incomplete()` 会将未完成 Turn 标记为失败，不能从 checkpoint 恢复业务内存。

### 4.4 Landscape

Landscape 是另一套独立 SQLite、工作流、任务管理器、证据包和报告逻辑。它复用了模型/Provider 的部分实现，但没有统一复用 IDEA 的冻结语料与上下文体系。作为业务边界可接受，作为 Agent 底座则形成明显重复。

## 5. 已具备的可靠基础

这些能力应保留并升级，不应重写：

- `StructuredModelClient` 的结构化输出与 schema 校验；
- publication number 归一化、抓取完整性校验和候选回填；
- 内容寻址的专利版本、run-version scope 和 corpus snapshot hash；
- lexical/vector 结果融合和显式 allowed version scope；
- `ContextAssembler` 对 Notes 与 Citation Evidence 的区分；
- Evidence alias、持久 ID、原文 offset/hash 和 Citation 的验证；
- 新颖性“单篇文献覆盖全部特征”等确定性业务规则；
- 输入快照、产物 hash、工具调用审计和调试信息脱敏；
- BYOK 不落库的安全边界。

## 6. 主要冗余与设计债

### P0：会阻碍 Agent 正确性的债务

#### 6.1 SQLite 与 PostgreSQL 双事实源

IDEA 的运行、特征和部分文档事实以 SQLite 为主；语料、chunk、引用、上下文和追问在 PostgreSQL。语料导入通过 prerequisite repository 把 SQLite 数据复制到 PostgreSQL。运行结束还需要额外同步状态。

影响：

- 事务边界跨数据库，失败后可能只写成一半；
- 删除 API 只删除 SQLite 和文件产物，没有联动删除 PostgreSQL 语料关联、引用、上下文和追问；
- Agent 决策如果继续双写，将难以可靠恢复和重放。

决策：新 Agent 领域一律以 PostgreSQL 为唯一事实源；旧 SQLite 只通过兼容适配器读取，逐步迁移。

#### 6.2 两层工作流状态

IDEA 同时使用 LangGraph checkpoint 和 `WorkflowHarness`；重试次数又分别存在 LangGraph RetryPolicy、SQLite attempt 记录和追问内存字典。当前流程本来是线性的，双层状态没有带来动态决策收益，反而使重启、attempt 和完成判定复杂化。

决策：保留一个工作流协调层。业务状态和动作日志落 PostgreSQL；LangGraph 只承载条件图执行，不作为业务事实源。

#### 6.3 上下文“token”计量不真实

`backend/idea/context.py` 的 `estimate_words()` 使用 `len(text.split())`。中文长文本可能被计为一个 token，48,000 的预算不具备约束意义。

决策：Context v2 必须使用与模型匹配的 tokenizer；无法获取时使用保守的字符/字节估算，并把 estimator 版本写入 manifest。

#### 6.4 不可恢复的追问内存

追问步骤间数据保存在 `_TurnMemory`；进程重启后直接失败。Agent 一旦拥有外部动作，这会产生重复检索、重复费用或状态丢失风险。

决策：决策、动作请求、动作结果、上下文快照、预算和 stop reason 全部持久化；动作使用 idempotency key。

### P1：显著增加维护成本的债务

#### 6.5 两套不兼容的 VectorIndex 协议

`backend/idea/ports.py` 与 `backend/idea/vector.py` 都定义 `VectorIndex/VectorHit`，请求和返回形状不同。实际 HybridRetriever 使用后一套。

决策：保留实际调用链使用的 version-scoped 接口，迁移实现和测试后删除另一套。

#### 6.6 Redis 被部署但未被使用

`redis_job_queue.py` 和 `redis_limiter.py` 有完整适配器及测试，Compose 也启动 Redis，但 `runtime.py` 没有装配它们。任务和限流仍是进程内实现，部署又固定一个 worker。

决策：在持久任务阶段真正接入队列/lease；在此之前从“必需运行组件”和 health 声明中移除 Redis，不能维持假装已接入的状态。

#### 6.7 重复证据包与上下文逻辑

IDEA 的直接分析路径、RAG 分析路径、追问和 Landscape 各有证据选择/组装逻辑。RAG 与非 RAG 两套文档分析路径也长期并存。

决策：抽取统一 `EvidenceService + ContextPolicy`，按 purpose 配置策略，不复制组装算法。

#### 6.8 状态迁移没有下沉到仓储

IDEA 的 `WorkflowHarness` 会验证迁移，但 `Database.set_run_status()` 本身不验证；任务管理器和异常处理可直接调用它绕开规则。Landscape 仓储反而有明确 allowed transitions。

决策：状态迁移的 compare-and-set 与 allowed transitions 在 repository 层原子执行。

### P2：配置和预留代码噪声

- `FeatureSettings` 仍暴露 deepdive、pct、value、cfp 等旧功能，但 validator 强制关闭。
- `document_store_dir` 当前实际运行链路未使用。
- `local_cache.enabled` 未控制是否把 CacheStore 传给 Provider。
- `app.host/port` 没有控制启动命令。
- `generate_docx_by_default/generate_xlsx_by_default` 未见真实生成链路。
- `logging.level/log_full_*` 没有驱动当前 logging 初始化。
- FileObjectStore、context adapter、迁移/导出/benchmark 工具中存在只服务测试或过渡期的边界。

决策：先用使用点测试标记 `active/deprecated/tool-only`，再删除；不要一次性清扫。

## 7. 当前系统为什么还不是 Agent

| Agent 能力 | 当前情况 | 缺口 |
|---|---|---|
| 目标理解 | 解析创意、分类追问 | 没有统一 Goal/Constraint |
| 自主决策 | 一次生成 query/followup plan | 不能根据 observation 再规划 |
| 工具使用 | 程序固定调用 Provider/RAG | 模型不能从受控工具集中选下一动作 |
| 上下文 | 有 Notes/Evidence 和 scope | 预算错误、无分层记忆和摘要策略 |
| 停止判断 | 固定步骤结束、候选饱和规则 | 无显式 stop policy/置信门 |
| 恢复 | IDEA 部分步骤可重跑 | 追问业务内存不可恢复 |
| 人工介入 | 用户选择模式/提交任务 | 缺少成本、扩域、重新研究审批门 |
| 评测 | 大量组件测试 | 无 Agent 决策轨迹和端到端评测集 |

## 8. 风险边界

- 专利原文、网页正文和历史对话都可能包含 prompt injection，必须作为数据引用而非指令。
- 专利结论具有法律风险。系统输出应定位为研究辅助，并在边界不满足时要求人工复核。
- “找不到证据”不能自动等价于“具有新颖性”。
- 新研究会扩大语料范围和成本，必须获得用户批准并建立 child run。
- BYOK 不持久化意味着重启恢复时必须暂停等待用户重新授权，不能把密钥写入 checkpoint。

## 9. 基线验证结果

- `python3 -m compileall -q backend tools`：通过。
- 虚拟环境全量 `unittest discover`：前 13 个 schema/agent 用例通过；随后停在 `test_api.IdeaApiTests.test_attachment_names_are_scoped_to_upload_directory`，45 秒超时。该结果只说明当前环境下全量基线未闭合，不能记录为“测试通过”或直接归因于业务失败。

升级 Phase 0 必须先定位这一 API 测试阻塞，并得到可重复的完整基线结果。

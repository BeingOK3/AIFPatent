# 专利全文语料、IDEA 评审与追问 RAG 架构设计

> 文档状态：待实现设计基线
>
> 版本：1.1
>
> 日期：2026-07-20
>
> 适用范围：AIFPatent 专利全文语料、首次 IDEA 评审、评审后追问、混合 RAG、方案变体与二次研究
>
> 当前系统前置文档：`../../docs/aifpatent-architecture.md`

## 1. 文档目的

本文档定义一套同时服务“首次 IDEA 评审”和“评审后追问”的耐久专利语料与 RAG 基础设施，并给出后续实现需要遵守的组件边界、数据模型、存储布局、RAG 流程、API、状态机、失败语义和实施顺序。

本设计不是在报告页面后简单增加通用聊天框。目标是建立：

> 以不可变专利版本和可定位原文为共同事实来源，在首次报告中完成按技术特征的证据检索，并允许后续围绕同一证据继续交互分析。

追问系统必须同时满足：

- 能解释已有评审结论；
- 新生成的首次评审报告直接复用结构化 RAG 证据管线；
- 能比较 IDEA 与一篇或多篇专利；
- 能提出有证据边界的技术规避候选；
- 能把修改后的 IDEA 显式升级为 Variant 或子 Run；
- 能在证据不足时触发新的检索，而不是依赖模型记忆；
- 每个专利事实和重合判断都能回到具体原文；
- 不修改原 Run、原报告和原结论；
- 不把技术相似自动表述为法律侵权结论。

本设计是目标架构，不代表当前运行时代码已经完成迁移。当前实现仍以 SQLite 和一次性 Evidence Packet 为主；在完成本文档 Phase 1～2 前，不能对外宣称已经持久保存全文，在完成 Phase 4 前不能宣称首次报告与追问已经统一使用完整混合 RAG。

## 2. 当前基线与必须解决的问题

### 2.1 已有能力

当前项目已经具备：

- 不可变 Case/Run 和固定 11 步 LangGraph Workflow；
- Google Patents 与 EXA 的候选检索和全文获取；
- 公开号、申请号和已知同族号去重；
- IDEA 的 F1..Fn 必要技术特征；
- 文献级技术特征映射；
- 带哈希的 Evidence；
- 确定性新颖性矩阵；
- 基于 D1/D2 证据的创造性分析；
- 报告、审计、Manifest 和调试事件。

这些数据已经为追问提供了良好起点，但还不是完整的问答语料库。

### 2.2 当前全文生命周期不支持稳定追问

`DocumentAnalysisService` 在单篇文献分析成功后会释放 `patent_documents` 中的摘要、权利要求和说明书全文，只保留元数据、全文哈希、Evidence 和特征映射。Provider 原始响应位于 1 GiB FIFO 缓存中，可能被自动淘汰。

因此，当前系统无法保证评审结束一段时间后仍能读取专利原文。追问功能实现前，必须建立不受 FIFO 清理影响的耐久专利语料库。

### 2.3 当前 `patent_documents` 是可更新行，不是内容版本

当前一条 `patent_documents` 记录由“公开号 + 语言”唯一标识，并可能在再次抓取时原地更新。这适合工作缓存，但不适合回答历史问题：

- 网页解析器升级后结构可能变化；
- 数据源可能补充或修改字段；
- 同一公开号不同时间抓到的文本可能不同；
- 历史 Run 必须明确引用当时分析过的内容版本。

因此必须把“专利身份”和“专利内容版本”分离，并让 Run 冻结引用具体版本。

### 2.4 当前没有可复用的检索索引

已有 Evidence 只覆盖模型在首次评审时使用过的片段，无法回答所有后续问题。例如用户询问实施例、从属权利要求或未进入原证据包的段落时，只查询 `evidence` 表会漏掉答案。

首次报告和追问都需要对深读专利的完整结构化文本建立：

- 章节化分块；
- 全文词法索引；
- 可选向量索引；
- 元数据过滤；
- 可复现的排序和上下文装配。

### 2.5 当前首次报告不是真正的 RAG

当前首次报告会抓取少量深读专利全文，再按关键词从权利要求、摘要和说明书中选择文本，拼装受字符预算限制的 Evidence Packet。它没有持久 Chunk、向量索引、混合召回、重排和可跨问题复用的 Citation 定位，因此属于临时证据压缩，而不是完整 RAG。

目标实现不得改变外部专利检索的职责：Google Patents/EXA 仍负责从开放语料中召回候选；RAG 从“选中深读文献已经抓取全文”之后开始，负责全文入库、结构化分块、特征级证据召回和后续问答。

### 2.6 模型凭证生命周期

当前 BYOK API Key 只存在于 Run 执行期间的进程内存，Run 结束后释放。追问不得绕过这一安全语义。每个追问请求必须重新携带当前页面的临时模型配置，或使用仍然存活的页面会话内存；凭证不得进入 PostgreSQL、日志、LangGraph State 或浏览器持久存储。

## 3. 目标与非目标

### 3.1 目标

第一阶段目标：

1. 深读文献抓取完成后，先持久化规范化全文和版本，再允许释放工作表中的临时全文；
2. 新 Run 的首次报告按 `技术特征 × 专利版本` 检索和组织证据；
3. 对 `COMPLETED` 和 `COMPLETED_WITH_LIMITATIONS` Run 创建追问 Thread；
4. 在本 Run 已深读文献范围内回答问题；
5. 支持指定单篇、多篇或全部深读文献；
6. 支持权利要求、摘要、说明书的原文检索；
7. 输出可点击、可校验的引用；
8. 支持技术重合解释、文献比较和技术规避候选；
9. 追问记录不可变追加，不覆盖原报告；
10. 全文按内容寻址、压缩和跨 Run 去重；
11. embedding 按文本哈希和模型版本复用；
12. 检索、模型调用、引用校验和失败都可审计；
13. 支持多进程、多 Worker 和多名访客同时使用；
14. 当前阶段采用公开共享空间，任何访客都能查看站内全部 Case、Run、报告和追问内容。

### 3.2 非目标

首个版本不包含：

- 自动给出正式侵权法律意见；
- 自动认定某项权利要求当前有效；
- 替代专利代理师或律师进行权利要求解释；
- 任意互联网开放式聊天；
- 默认对评审范围外的专利作事实判断；
- 用户账号、组织、团队、分组、租户隔离和细粒度 ACL；
- 初期引入 Qdrant 或 Elasticsearch；
- 让追问修改原 Run 的新颖性、创造性或价值结论；
- 未经用户确认就自动创建新检索 Run。

## 4. 核心产品语义

### 4.1 三类操作必须分离

追问入口看似统一，后端必须区分三类操作：

| 模式 | 证据范围 | 是否改变 IDEA | 是否外部检索 | 结果性质 |
|---|---|---:|---:|---|
| `EVIDENCE_QA` | 原 Run 深读文献 | 否 | 否 | 对已有证据的解释 |
| `DESIGN_AROUND` | 原 Run 深读文献 | 生成候选但不替换 | 否 | 技术规避候选 |
| `NEW_RESEARCH` | 原 Run + 新查询 | 可选 | 是 | 新的子 Run/研究任务 |

用户输入如果实质上修改了 IDEA，例如“把 F3 改成云端计算后是否仍有风险”，系统可以先在 `DESIGN_AROUND` 模式说明预期影响，但必须提示：正式结论需要把修改保存为 Variant 并创建子 Run 重新检索。

### 4.2 “冲突”的标准化表达

系统内部和用户界面统一区分：

- `TECHNICAL_OVERLAP`：技术特征或技术手段重合；
- `NOVELTY_RELEVANCE`：与单篇现有技术覆盖有关；
- `INVENTIVE_STEP_RELEVANCE`：与 D1/D2 组合有关；
- `POTENTIAL_CLAIM_RELEVANCE`：可能与权利要求要素相关；
- `LEGAL_REVIEW_REQUIRED`：需要有效权利要求、法域和法律解释，系统不作确定结论。

回答不得把 `TECHNICAL_OVERLAP` 自动改写为“侵权”“落入保护范围”或“已经规避”。

### 4.3 原报告不可变

追问是原 Run 的派生记录，不是原 Workflow 的第 12 个节点。原报告、原结论、原审计结果和原 Manifest 均不得被追问修改。

如果追问发现原报告可能存在问题，应记录为：

- `FOLLOWUP_WARNING`；
- 用户可见的不一致说明；
- 可选的“重新运行”建议。

不得静默回写历史报告。

“原报告不可变”适用于已经生成的历史报告。完成 RAG 改造后，新 Run 可以使用新的证据管线生成报告，但必须记录 `analysis_evidence_mode`、检索器版本、Chunker 版本、embedding 版本和冻结的专利 Version，保证新旧报告可区分、可复现。

### 4.4 公开共享访问模型

当前产品阶段不建设用户体系或分组权限，采用单一公开共享空间：

- 不创建 `user_id`、`organization_id`、`tenant_id`、成员关系或可见性 ACL；
- Case、Run、报告、专利语料状态、Thread、Turn 和 Citation 对所有网站访客可见；
- Run/Thread 的文献范围是证据边界，不是权限边界；
- 列表和读取 API 不按访问者过滤数据；
- BYOK API Key 仍然只属于当前请求内存，绝不能因为内容公开而被保存、展示或复用；
- 删除、全量导出、Corpus GC 等破坏性运维操作不放入普通公开 UI，由部署运维入口控制；
- 页面必须持续提示：提交的 IDEA、报告和追问会公开展示，不应提交商业秘密或个人敏感信息。

公开访问不等于无限调用。匿名请求仍需按 IP/设备信号实施速率限制、并发上限和成本预算，但这些机制不产生内容可见性分组。

## 5. 总体架构

```text
┌────────────────────────────────────────────────────────────────────┐
│ Public IDEA Web UI                                                 │
│ 全部 Case/Run/报告 / 专利卡片 / Thread / Citation / Variant       │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ HTTP + SSE
┌──────────────────────────────▼─────────────────────────────────────┐
│ API + Workflow/Job Manager                                        │
│ 首次评审 / 追问 / BYOK 临时凭证 / 取消 / 事件 / 匿名限流          │
└───────────────┬───────────────────────────┬────────────────────────┘
                │                           │
┌───────────────▼────────────────┐  ┌───────▼────────────────────────┐
│ Evaluation Evidence Pipeline   │  │ Follow-up Workflow             │
│ Feature×Patent 强制覆盖 + RAG  │  │ Scope→Plan→Retrieve→Answer     │
└───────────────┬────────────────┘  └───────┬────────────────────────┘
                └───────────────┬────────────┘
                                │
┌───────────────────────────────▼────────────────────────────────────┐
│ Patent Corpus Service / Hybrid Retriever / Citation Verifier      │
│ 版本、内容哈希、结构化 Chunk、PostgreSQL FTS、pgvector、RRF       │
└───────────────┬───────────────────────────┬────────────────────────┘
                │                           │
┌───────────────▼────────────────┐  ┌───────▼────────────────────────┐
│ PostgreSQL + pgvector          │  │ S3/MinIO Object Store          │
│ 业务事实/FTS/向量/审计/引用    │  │ 规范化全文及可选原始文件        │
└────────────────────────────────┘  └────────────────────────────────┘
                                │
┌───────────────────────────────▼────────────────────────────────────┐
│ Redis                                                              │
│ Job Queue / 分布式锁 / Provider 全局限流 / 短期状态与成本预算      │
└────────────────────────────────────────────────────────────────────┘
```

目标生产部署以 PostgreSQL 为业务事实来源，pgvector 与业务过滤在同一事务边界内工作；S3 兼容对象存储保存大正文；Redis 负责实时协调。现有 SQLite 是迁移前的运行基线，不再作为新 RAG 子系统的生产目标。单机开发可以使用容器化 PostgreSQL/pgvector，并通过 `ObjectStore` 的本地文件实现替代 MinIO；不得为方便开发重新引入另一套 SQLite Schema。

### 5.1 组件责任

#### `PatentCorpusService`

负责：

- 验证 `FetchedDocument`；
- 生成规范化文档；
- 计算内容哈希；
- 创建不可变内容版本；
- 压缩并写入内容寻址对象；
- 结构化分块；
- 建立 PostgreSQL FTS/`pg_trgm` 与 pgvector 索引；
- 调度或延迟生成 embedding；
- 将 Run 绑定到确切文档版本；
- 验证 Blob、Chunk 和向量完整性；
- 重新抓取后创建新版本而不是覆盖旧版本。

#### `EvaluationEvidencePipeline`

负责首次报告中的共享 RAG 使用：

- 接收既有查询规划器输出和合并后的候选集，不取代 Google Patents/EXA 外部召回；
- 只对选入深度分析的专利抓取、冻结和索引全文；
- 对每个 `F_i × D_j` 独立检索权利要求和说明书证据；
- 强制覆盖所有独立权利要求，并为命中的从属权利要求补齐父权利要求链；
- 将受版本约束的 Chunk 组装为首次分析证据包；
- 把检索命中、最终引用和检索器版本写入审计表；
- 继续把特征映射交给确定性新颖性矩阵，不允许跨文献拼接新颖性。

#### `FollowupScopeService`

负责：

- 验证源 Run 已到允许终态；
- 解析用户选择的公开号；
- 把范围限制为本 Run 的深读文档版本；
- 冻结 `corpus_snapshot_hash`；
- 防止模型扩大到冻结证据范围外的文献；
- 判断某个问题是否必须升级为 `NEW_RESEARCH`。

#### `HybridRetriever`

负责：

- 查询改写后的词法召回；
- 可选向量召回；
- 元数据过滤；
- Reciprocal Rank Fusion；
- 章节权重；
- 去重；
- 可选重排；
- 生成可审计 Retrieval Hit。

同一实现同时服务首次报告和追问。调用方必须传入明确的 `purpose=INITIAL_REVIEW | FOLLOWUP`、允许的 Version ID、Feature/Question 和章节策略，Retriever 不得自行扩大到全站 Corpus。

#### `ContextAssembler`

负责：

- 按 token/字符预算装配证据；
- 保留公开号、章节、权利要求号、偏移和 Chunk ID；
- 优先纳入用户指定专利；
- 加入 IDEA F1..Fn、原特征映射和原结论；
- 不把不受信任的专利文本当作模型指令；
- 避免把全部聊天历史直接拼入提示词。

#### `FollowupAnswerService`

负责受限结构化生成：

- 直接回答；
- IDEA—专利重合矩阵；
- 差异和不确定性；
- 技术规避候选及代价；
- 是否需要新检索或人工法律复核；
- 引用别名。

#### `CitationVerifier`

负责确定性校验：

- 引用是否属于本次检索结果；
- Chunk 是否属于允许的文档版本；
- 引用原文是否与 Chunk 内容一致；
- quote hash 是否匹配；
- 公开号和文档版本是否匹配；
- 关键判断是否至少有一个引用；
- 模型是否引入未知公开号；
- 规避建议是否被错误描述为确定法律结论。

#### `JobCoordinator`

负责把长任务从 Web 进程中解耦，并通过 Redis 协调：

- Run、全文入库、Chunk、embedding 和 Follow-up Turn 的异步任务；
- 幂等键、任务租约、重试和取消；
- Google Patents 按“出口路由 + Origin”共享的全局串行锁、请求时间和熔断状态；
- embedding/LLM 并发和匿名调用预算。

Google 的实时锁和冷却状态必须跨所有 Web/Worker 进程共享，不能继续只依赖进程内 `asyncio.Lock`。需要审计和重启后保留的熔断事实写入 PostgreSQL；Redis 负责低延迟协调。

## 6. Follow-up Workflow

### 6.1 固定节点

建议为追问建立独立的短流程，不复用 IDEA 的 11 个业务节点：

```text
START
  -> PREPARE_FOLLOWUP_SCOPE
  -> CLASSIFY_AND_PLAN
  -> RETRIEVE_FOLLOWUP_EVIDENCE
  -> ASSEMBLE_FOLLOWUP_CONTEXT
  -> GENERATE_FOLLOWUP_ANSWER
  -> VERIFY_FOLLOWUP_ANSWER
  -> PERSIST_FOLLOWUP_RESPONSE
  -> END
```

Graph State 只保存：

- `turn_id`；
- `thread_id`；
- `run_id`；
- 最后完成节点；
- 完成节点数。

问题全文、专利全文、API Key、向量和模型上下文不得进入 LangGraph Checkpoint。

### 6.2 状态机

Thread 状态：

- `ACTIVE`；
- `ARCHIVED`。

Turn 状态：

- `QUEUED`；
- `RUNNING`；
- `COMPLETED`；
- `COMPLETED_WITH_LIMITATIONS`；
- `FAILED`；
- `CANCELLED`。

Turn 必须独立重试。某一轮失败不得损坏前面的对话记录。

服务重启后，正在运行且凭证已丢失的 Turn 进入：

```text
FAILED / RUNTIME_API_KEY_REQUIRED_AFTER_RESTART
```

不得在没有用户重新提供凭证时自动恢复模型调用。

### 6.3 意图路由

`CLASSIFY_AND_PLAN` 输出严格 Schema：

```json
{
  "mode": "EVIDENCE_QA",
  "question_type": "OVERLAP_EXPLANATION",
  "selected_publication_numbers": ["CN..."],
  "query_rewrites": ["...", "..."],
  "preferred_sections": ["claims", "description"],
  "required_features": ["F1", "F3"],
  "requires_new_research": false,
  "requires_legal_review": false,
  "rationale": "..."
}
```

后端必须重新验证模型返回的公开号和 feature ID，不得直接信任路由 Agent。

### 6.4 对话上下文

每轮都重新执行证据检索，不允许仅依据聊天记忆回答专利事实。

上下文建议包含：

- Thread 的冻结范围；
- IDEA 的 F1..Fn；
- 当前问题；
- 最近 3～5 轮对话或经过验证的对话摘要；
- 与问题相关的原评审特征映射；
- 本轮重新检索的证据块；
- 原 Run 的结论和限制。

历史回答不是权威证据。历史回答中的专利事实必须在本轮证据中重新得到支持。

## 7. 耐久专利语料库设计

### 7.1 保存边界

全文持久化从深度分析文献开始，不把每个外部搜索命中都抓成全文：

| 阶段 | 默认保存内容 | 是否建立全文 RAG 索引 |
|---|---|---:|
| 外部候选召回 | 标题、摘要片段、公开号/申请号、日期、分类、URL、命中查询 | 否 |
| 合并和初筛 | 候选元数据、去重关系、筛选分数和理由 | 否 |
| 深度分析入选 | 规范化摘要、完整权利要求、说明书、稳定元数据和来源记录 | 是 |
| 追问触发的新研究 | 只有显式进入新研究并被选为深读文献后才保存全文 | 是 |
| 官方 PDF/XML/原始 HTML | 按来源授权和审计需求可选保存 | 不直接索引，先解析为规范化版本 |

这样既保证报告和追问能长期复用原文，也避免对几十到上百个弱相关候选进行不必要的全文抓取、存储和 embedding。

### 7.2 三层身份

必须区分：

1. `patent_document`：专利身份，例如 `CN123456A/zh`；
2. `patent_document_version`：某次规范化全文的不可变版本；
3. `corpus_blob`：按 SHA-256 内容寻址的压缩对象。

同一专利再次抓取时：

- 内容哈希相同：复用已有 Version 和 Blob，只新增来源记录；
- 内容哈希不同：创建新 Version；
- Run 始终引用其当时使用的 Version；
- 不能更新旧 Version 的正文或 Chunk。

### 7.3 规范化内容

MVP 的耐久原文对象保存结构化规范化 JSON，而不是依赖 Provider HTML：

```json
{
  "schema_version": "1.0",
  "publication_number": "CN...",
  "language": "zh",
  "metadata": {},
  "abstract": {"text": "...", "spans": []},
  "claims": {"text": "...", "spans": []},
  "description": {"text": "...", "spans": []}
}
```

内容哈希只能覆盖稳定、规范化的专利内容字段。`provider`、`source_url`、`retrieved_at`、HTTP 请求时间、出口 IP、抓取任务 ID 等易变来源字段必须进入 `patent_version_sources`，不得参与正文哈希；否则同一份正文每次抓取都会被误判成新版本。`parser_version` 和规范化规则版本需要记录，但内容未变化时不得仅因重新抓取而重复保存 Blob。

稳定 JSON 使用确定性序列化后计算 SHA-256，再使用 gzip 或 zstd 压缩。压缩编码不是内容身份的一部分，同一内容可以迁移编码而不改变 `normalized_content_hash`。

### 7.4 对象存储布局

生产环境使用 S3/MinIO 兼容对象存储，开发环境允许使用实现相同 `ObjectStore` 接口的本地目录。对象 Key 保持内容寻址：

```text
patent-corpus/
├── normalized/sha256/ab/cd/abcdef....json.zst
├── raw/sha256/ab/cd/abcdef....xml.gz       # 可选
├── staging/                                 # 临时对象
└── quarantine/                              # 校验失败对象
```

配置建议新增：

```json
{
  "storage": {
    "object_backend": "s3",
    "bucket": "aifpatent-corpus",
    "prefix": "patent-corpus/"
  }
}
```

本地开发配置可以使用 `object_backend=filesystem` 和 `workspace/patent-corpus`，但启动校验必须拒绝其位于 FIFO `cache_dir` 内。现有 `document_store_dir=workspace/cache/documents` 不得直接作为耐久语料库使用。

### 7.5 幂等写入

Blob 写入流程：

1. 生成稳定正文哈希和目标对象 Key；
2. 上传到 staging 或使用条件写入，完成后复算解压内容哈希；
3. 使最终内容寻址对象可读，存在时验证并复用，不覆盖不同内容；
4. 在 PostgreSQL 事务中插入或复用 Blob、Version、Source 和 Run Link；
5. 数据库提交前不得把 Version 标记为 `READY`；
6. 失败任务可以安全重试，后台清理由引用状态回收孤立 staging 对象。

本地文件实现使用临时文件、`fsync` 和原子重命名；S3/MinIO 实现使用不可变 Key、条件写/存在性校验和“先对象可读、后数据库提交”的顺序。不能假设对象存储支持跨对象与 PostgreSQL 的分布式事务。

文件已经存在但数据库记录缺失时，启动 repair 可以补建索引；数据库存在但文件缺失时，Version 进入 `CORRUPT`，不得用于问答。

### 7.6 去重层次

采用四层去重：

| 层次 | Key | 作用 |
|---|---|---|
| 专利身份 | 规范化公开号 + 语言 | 合并同一文献身份 |
| 内容版本 | 规范化全文 SHA-256 | 判断是否为相同内容 |
| Chunk | 版本 + 章节 + 标签 + 偏移 + chunker 版本 | 稳定定位 |
| Embedding | Chunk 文本哈希 + embedding 模型/版本 | 跨 Run、跨文献复用向量 |

同样的标准段落可能出现在多个同族或引用文献中。允许多个 Chunk 指向同一个 embedding 记录，但必须保留各自的公开号和章节位置。

### 7.7 与当前全文释放逻辑的集成点

`NORMALIZE_AND_FETCH` 成功获得并验证 `FetchedDocument` 后，应先执行幂等语料入库和 Run-Version 绑定，再允许 `ANALYZE_DOCUMENTS` 在成功后释放 `patent_documents` 临时全文。

推荐顺序：

```text
Provider Fetch
→ Parse + Contract Validate
→ Persist transient patent_documents
→ Corpus ingest + version freeze
→ Patent-aware chunk + lexical index
→ Embedding index（可降级或异步补齐）
→ Feature × Patent RAG analysis + Evidence
→ Release transient full text
```

如果 Corpus ingest 失败：

- 首期应让 `NORMALIZE_AND_FETCH` 失败并重试；
- 不得继续释放唯一全文副本；
- 不得把 Run 标记为“支持 RAG 评审/追问”。

为兼容历史 Run，可增加 `corpus_availability`：

- `READY`：已经冻结 Version；
- `REHYDRATABLE`：无 Version，但存在可重新抓取的公开号；
- `UNAVAILABLE`：无法取得完整语料；
- `CORRUPT`：记录或 Blob 未通过哈希校验。

## 8. 专利分块设计

### 8.1 分块原则

专利不能只按固定 token 数机械切割。Chunk 必须保留专利结构：

- 摘要：通常作为一个 Chunk；
- 独立权利要求：每项单独一个 Chunk；
- 从属权利要求：每项一个 Chunk，并记录依附链；
- 发明内容：按有语义边界的段落组合；
- 具体实施方式：按标题、实施例、段落和附图引用组合；
- 背景技术：单独标记，不能与权利要求等权；
- 过长段落：按句子边界滑窗切分，并保留重叠。

### 8.2 建议参数

初始参数：

- 目标大小：800～1,500 tokens；
- 最大大小：2,000 tokens；
- 滑窗重叠：100～200 tokens；
- 摘要和单项权利要求尽量不拆分；
- 单项权利要求过长时保留共同的 claim label；
- 描述分块保留段落号、实施例名和附图号。

这些值进入配置和 `chunker_version`，不得散落硬编码。

### 8.3 Chunk 身份

```text
chunk_id = SHA256(
  document_version_id |
  section_type |
  section_label |
  start_offset |
  end_offset |
  chunker_version
)
```

每个 Chunk 保存：

- `document_version_id`；
- `publication_number` 冗余索引字段；
- `section_type`；
- `section_label`；
- `claim_number`；
- `claim_kind`；
- `parent_claim_numbers`；
- `start_offset` / `end_offset`；
- `text`；
- `text_hash`；
- `token_count`；
- `chunker_version`。

偏移必须针对 Version 中相应章节的规范化全文，而不是压缩文件字节偏移。

## 9. 混合 RAG 检索设计

### 9.1 PostgreSQL + pgvector 作为统一检索底座

首次报告和追问都先按 Run/Version 过滤。一次分析通常只涉及几十篇深读专利，即使全站 Corpus 持续增长，单次进入最终排序的集合仍然可控。

目标实现使用：

- PostgreSQL 保存业务事实、版本、Chunk、检索命中、报告和对话；
- PostgreSQL `tsvector`/GIN 与 `pg_trgm` 负责词法、术语和编号召回；
- pgvector 负责跨语言语义向量召回；
- SQL 在召回阶段同时执行 Version、章节、语言、时间等元数据过滤；
- 小范围使用精确向量排序，数据和 QPS 增长后在 pgvector 内增加 HNSW/IVFFlat 索引；
- 保留 `VectorIndex` 抽象，但首个生产实现固定为 `PgVectorIndex`。

这一方案避免在首期同时维护 PostgreSQL 和独立向量数据库的双写一致性。只有 pgvector 的容量、延迟或运维指标明确不能满足需求时，才通过 ADR 评估 Qdrant；当前不引入 Elasticsearch。

### 9.2 EmbeddingProvider 抽象

接口至少包含：

```python
class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
```

设计约束：

- 中文和英文专利必须使用同一跨语言向量空间；
- 持久记录 provider、model、dimensions 和规范化方式；
- 模型变化不能覆盖旧向量；
- 文档 embedding 可以延迟生成；
- 没有 embedding 能力时允许降级为 PostgreSQL 词法检索，但报告/回答必须记录 `LEXICAL_ONLY` limitation；
- 共享 Corpus 使用部署级、版本化的 active embedding profile，不能接受每个 Run/Turn 随意选择不同模型；
- query embedding 必须使用与目标 Chunk 相同的 profile，模型或维度不同的向量不能混合排序；
- embedding API Key 通过部署 Secret 注入，不写数据库；Chat 模型仍可使用每次请求携带的 BYOK；
- 任何远程 embedding 都必须明确提示数据会发送到外部模型服务。

更换 embedding 模型时创建新的 profile 和重建任务，不覆盖旧向量。pgvector 索引按固定维度的 profile 建表或分区；只有新 profile 达到完整率和离线评测门槛后才切换 active profile。

### 9.3 查询规划

查询规划输入：

- 用户原问题；
- IDEA F1..Fn；
- 用户指定公开号；
- 原评审映射；
- 问题类型；
- 最近对话摘要。

输出两组查询：

- `lexical_queries`：保留专业词、缩写、权利要求要素和中英文同义表达；
- `semantic_query`：用于 embedding 的完整问题语义表达。

查询规划不能加入一个不属于 IDEA、历史对话或用户问题的新技术事实。

### 9.4 强制范围过滤

所有检索必须先从 `run_document_versions` 得到允许的 Version ID，再执行召回：

```text
allowed_version_ids = versions deep-reviewed by source run
                        ∩ user selected documents
```

范围过滤必须在检索层完成，不能仅在 Prompt 中要求模型忽略其他文献。

### 9.5 词法召回

使用 PostgreSQL 生成列或受控索引任务维护 `search_tsv`，并为标识符和子串建立 `pg_trgm` 索引。参与检索的字段包括：

- `text`；
- `publication_number`；
- `section_type`；
- `section_label`；
- 可选 `title`。

中文技术术语由应用层的版本化 tokenizer 生成规范化 token，再写入 `tsvector`；英文使用 PostgreSQL 的受控 text search configuration。公开号、缩写、化学式和短术语同时走精确匹配/`pg_trgm`，不能只依赖自然语言分词。

查询必须使用参数化 SQL，并由程序生成 `tsquery`，不允许把用户字符串直接作为任意全文表达式执行。

初始召回建议：

- 每个 lexical query Top 30～50；
- 合并后按 Chunk ID 去重；
- 保存原始 BM25 rank 和查询来源。

### 9.6 向量召回

默认 `PgVectorIndex`：

1. 用参数化 SQL 限定允许的 Version、embedding 模型和维度；
2. 对过滤后的 `vector` 列执行 cosine distance 排序；
3. 小范围优先精确搜索，达到基准阈值后再启用近似索引；
4. 返回 Top 30～50，并保存 distance、rank 和索引模式；
5. 不把向量本身写入日志、API 响应或 Graph State。

业务服务通过 `VectorIndex` 接口调用 pgvector，不在 Workflow 或 Agent 中拼接数据库 SQL。近似索引上线前必须验证过滤后召回率，不能为了降低延迟牺牲关键权利要求证据。

### 9.7 融合排序

BM25 和余弦分数尺度不同，MVP 不直接线性相加，使用 Reciprocal Rank Fusion：

```text
RRF(chunk) = Σ 1 / (k + rank_i)
```

初始 `k=60`，进入配置。

随后应用可解释的章节权重：

| 问题类型 | 优先章节 |
|---|---|
| 权利要求覆盖/重合 | 独立权利要求 > 从属权利要求 > 发明内容 > 实施例 |
| 技术原理解释 | 发明内容 > 实施例 > 摘要 > 权利要求 |
| 新颖性解释 | 独立权利要求/明确披露段落 > 摘要 > 其他 |
| 规避候选 | 权利要求 + 实施例 + IDEA 差异特征 |

章节权重只能影响检索排序，不能把没有原文支持的内容变成证据。

### 9.8 语义重排

可选 Reranker 只处理融合后的 Top 20～30，输出 Top 8～15 给模型。

Reranker 可以是：

- 本地 cross-encoder；
- 受限 LLM relevance classifier；
- 外部 rerank API。

Reranker 必须输出 `relevant / partial / irrelevant` 和理由，不得生成专利结论。不可用时保持 RRF 排序并记录 limitation。

### 9.9 多样性与去重

上下文装配前进行：

- 完全相同 `text_hash` 去重；
- 同一 claim 的滑窗块限制数量；
- 单篇专利最大 Chunk 数限制；
- 比较问题确保每篇指定专利至少有最低证据配额；
- 权利要求与说明书保留适当多样性。

避免 Top-K 全部被同一篇专利或同一项超长权利要求占满。

### 9.10 首次评审如何使用共享 RAG

RAG 不替代现有的查询式生成和外部搜索。新 Run 的完整数据流固定为：

```text
IDEA 解析为 F1..Fn
→ 查询规划器生成中英文术语、同义词、上位词、分类号和多轮查询
→ Google Patents/EXA 返回候选元数据和摘要片段
→ 公开号/申请号/同族/URL 归一化去重
→ 元数据与摘要初筛，选择深度分析文献
→ 只为深度分析文献抓取并校验全文
→ 全文 Version/Blob 持久化、结构化 Chunk、词法与向量索引
→ 对每个 F_i × D_j 召回和组装证据
→ LLM 输出特征映射与 Citation
→ 确定性新颖性矩阵、创造性分析和报告
```

对每个 `F_i × D_j` 分别检索，防止某个强势技术特征占满整篇专利的上下文。检索命中写入 `report_retrieval_hits`；进入最终结论的证据继续写入 Evidence，并增加 `version_id/chunk_id` 定位。已有 11 步 Workflow 不增加新节点：Corpus ingest 集成在 `NORMALIZE_AND_FETCH` 内，特征级检索集成在 `ANALYZE_DOCUMENTS` 内。

### 9.11 专利分析的强制覆盖规则

首次报告不能只依赖普通 Top-K：

- 每篇深读专利的摘要固定纳入基础阅读；
- 每项独立权利要求必须至少经过一次单独评估，过长时可分块但必须汇总结论；
- 命中的从属权利要求必须补齐父权利要求链；
- 说明书、实施例和背景技术使用混合 RAG 召回；
- `NOT_DISCLOSED` 不能仅由“Top-K 没有命中”推出，必须结合强制权利要求覆盖和检索充分性判断；
- embedding 不可用时允许词法降级，但必须记录限制；
- 新颖性仍按单篇文献是否披露全部必要特征确定，不允许把不同文献的片段拼接成新颖性否定。

因此，RAG 在首次报告中负责证据定位、压缩和组织，而不是替代确定性法律/业务规则。

## 10. 回答和引用契约

### 10.1 结构化回答

模型输出建议：

```json
{
  "answer_type": "OVERLAP_ANALYSIS",
  "direct_answer": "...",
  "overlap_items": [
    {
      "feature_id": "F1",
      "idea_feature": "...",
      "patent_element": "...",
      "overlap_level": "HIGH",
      "analysis": "...",
      "citation_aliases": ["C1"]
    }
  ],
  "differences": [],
  "design_around_options": [
    {
      "title": "...",
      "change": "...",
      "target_features": ["F1"],
      "expected_effect": "...",
      "engineering_tradeoffs": ["..."],
      "remaining_risks": ["..."],
      "citation_aliases": ["C1", "C3"],
      "requires_new_search": true
    }
  ],
  "legal_boundary": "...",
  "limitations": [],
  "needs_new_research": false
}
```

### 10.2 Citation Packet

模型只看到短别名 `C1..Cn`。每个别名由后端绑定：

```json
{
  "alias": "C1",
  "chunk_id": "...",
  "document_version_id": "...",
  "publication_number": "CN...",
  "title": "...",
  "section_type": "claims",
  "section_label": "claim 1",
  "source_url": "...",
  "quote_text": "...",
  "quote_hash": "..."
}
```

模型不得输出数据库内部永久 Citation ID，只能引用当前 Packet 的别名。后端成功验证后再生成永久 `citation_id`。

### 10.3 确定性门禁

以下情况 Turn 必须失败或降级，不得伪成功：

- 引用未知别名；
- 引用不属于允许范围；
- quote 与 Chunk 不一致；
- Chunk/Blob 哈希错误；
- 回答引入未知公开号；
- `HIGH` 技术重合但没有引用；
- 引用只来自背景技术，却声称某权利要求明确覆盖；
- 将技术建议表述为确定不侵权；
- 用户要求原文事实但语料不可用。

允许一次带合法 Citation 清单的模型纠错；第二次仍不通过则失败。

### 10.4 引用展示

前端点击引用时展示：

- 专利标题和公开号；
- 章节/权利要求号；
- 命中的原文；
- 上下文前后段；
- Google Patents 或授权数据源链接；
- 文档版本抓取时间；
- “这是技术分析，不是正式法律意见”的边界提示。

## 11. 建议数据模型

以下为 PostgreSQL 目标逻辑 Schema。实现时使用版本化迁移，把现有 SQLite 数据导入 PostgreSQL 并进行行数、主键、哈希和报告可读性校验；切换完成前旧库只作为兼容来源，不在 SQLite 中继续扩展一套平行 RAG Schema。

字段示例中的 `TEXT/INTEGER` 表达业务含义；实际 PostgreSQL DDL 优先使用 `UUID`、`TIMESTAMPTZ`、`JSONB`、`BOOLEAN` 和受约束 Enum/Check。所有外键、删除行为和常用范围过滤索引必须在迁移中显式声明。当前公开共享模式不在业务表上增加 `user_id/tenant_id/organization_id`。

### 11.1 语料表

#### `patent_document_versions`

```text
version_id                 TEXT PRIMARY KEY
document_id                TEXT NOT NULL -> patent_documents
language                   TEXT NOT NULL
normalized_content_hash    TEXT NOT NULL
normalized_blob_hash       TEXT NOT NULL -> corpus_blobs
parser_version             TEXT NOT NULL
schema_version             TEXT NOT NULL
state                      READY | CORRUPT | QUARANTINED
metadata_json              TEXT NOT NULL
created_at                 INTEGER NOT NULL
UNIQUE(document_id, language, normalized_content_hash)
```

`normalized_content_hash` 只由稳定规范化正文计算；`parser_version` 用于解释该 Version 如何生成，不把抓取时间或来源请求信息混入哈希。

#### `patent_version_sources`

```text
source_id                  TEXT PRIMARY KEY
version_id                 TEXT NOT NULL
provider                   TEXT NOT NULL
source_url                 TEXT NOT NULL
retrieved_at               INTEGER NOT NULL
raw_response_hash          TEXT NULL
parser_version             TEXT NOT NULL
metadata_json              TEXT NOT NULL
```

#### `corpus_blobs`

```text
blob_hash                  TEXT PRIMARY KEY
encoding                   gzip | zstd
object_key                 TEXT NOT NULL UNIQUE
uncompressed_bytes         INTEGER NOT NULL
compressed_bytes           INTEGER NOT NULL
content_type               TEXT NOT NULL
state                      READY | CORRUPT | MISSING
created_at                 INTEGER NOT NULL
verified_at                INTEGER
```

#### `run_document_versions`

```text
run_id                     TEXT NOT NULL
document_id                TEXT NOT NULL
version_id                 TEXT NOT NULL
deep_reviewed              INTEGER NOT NULL
corpus_availability        READY | REHYDRATABLE | UNAVAILABLE | CORRUPT
linked_at                  INTEGER NOT NULL
PRIMARY KEY(run_id, document_id)
```

#### `patent_chunks`

```text
chunk_id                   TEXT PRIMARY KEY
version_id                 TEXT NOT NULL
publication_number         TEXT NOT NULL
section_type               TEXT NOT NULL
section_label              TEXT NOT NULL
claim_number               TEXT
claim_kind                 independent | dependent | unknown
parent_claims_json         TEXT NOT NULL
start_offset               INTEGER
end_offset                 INTEGER
text                       TEXT NOT NULL
text_hash                  TEXT NOT NULL
token_count                INTEGER NOT NULL
chunker_version            TEXT NOT NULL
metadata_json              TEXT NOT NULL
created_at                 INTEGER NOT NULL
UNIQUE(version_id, section_type, section_label, start_offset, end_offset, chunker_version)
```

`patent_chunks` 增加 `search_tsv TSVECTOR`。对 `search_tsv` 建 GIN，对需要子串匹配的规范化文本/标识符建 `pg_trgm` 索引；向量独立保存在版本化 embedding 表中。索引 repair 测试必须验证 Chunk、词法索引和 embedding 版本一致。

#### `embedding_profiles`

```text
profile_id                 TEXT PRIMARY KEY
provider                   TEXT NOT NULL
model                      TEXT NOT NULL
dimensions                 INTEGER NOT NULL
normalization              TEXT NOT NULL
state                      BUILDING | ACTIVE | RETIRED | FAILED
created_at                 INTEGER NOT NULL
activated_at               INTEGER
UNIQUE(provider, model, dimensions, normalization)
```

#### `embedding_vectors`

```text
embedding_id               TEXT PRIMARY KEY
text_hash                  TEXT NOT NULL
profile_id                 TEXT NOT NULL -> embedding_profiles
embedding                  VECTOR NOT NULL
vector_norm                REAL NOT NULL
created_at                 INTEGER NOT NULL
UNIQUE(text_hash, profile_id)
```

物理实现按 `profile_id`/固定维度分区或建专用表，并使用 `VECTOR(<dimensions>)` typmod，保证每个 pgvector 索引只包含同维度向量。active profile 切换通过配置和事务完成，不原地覆盖旧向量。

#### `chunk_embeddings`

```text
chunk_id                   TEXT NOT NULL
embedding_id               TEXT NOT NULL
PRIMARY KEY(chunk_id, embedding_id)
```

#### `report_retrieval_hits`

记录首次报告中每个技术特征与每篇深读专利的检索过程，避免报告只有最终模型结论而没有 RAG 审计轨迹：

```text
run_id                     TEXT NOT NULL
version_id                 TEXT NOT NULL
feature_id                 TEXT NOT NULL
chunk_id                   TEXT NOT NULL
selection_reason           forced_abstract | forced_claim | lexical | vector | rerank
lexical_rank               INTEGER
vector_rank                INTEGER
rrf_score                  REAL
rerank_score               REAL
selected_for_context       INTEGER NOT NULL
retriever_version          TEXT NOT NULL
created_at                 INTEGER NOT NULL
PRIMARY KEY(run_id, version_id, feature_id, chunk_id, selection_reason)
```

现有 Evidence/Feature Mapping 需要新增 `version_id` 和可空 `chunk_id`，使报告中的最终证据能够回到冻结全文。无法对应单一 Chunk 的组合证据使用独立关联表，不得把多个原文拼成一个伪造偏移。

### 11.2 对话表

#### `followup_threads`

```text
thread_id                  TEXT PRIMARY KEY
run_id                     TEXT NOT NULL
title                      TEXT NOT NULL
status                     ACTIVE | ARCHIVED
default_mode               TEXT NOT NULL
scope_json                 TEXT NOT NULL
corpus_snapshot_hash       TEXT NOT NULL
created_at                 INTEGER NOT NULL
updated_at                 INTEGER NOT NULL
```

#### `followup_turns`

```text
turn_id                    TEXT PRIMARY KEY
thread_id                  TEXT NOT NULL
parent_turn_id             TEXT
status                     TEXT NOT NULL
mode                       TEXT NOT NULL
question_text              TEXT NOT NULL
question_hash              TEXT NOT NULL
scope_json                 TEXT NOT NULL
plan_json                  TEXT
answer_json                TEXT
model                      TEXT NOT NULL
prompt_version             TEXT NOT NULL
retriever_version          TEXT NOT NULL
corpus_snapshot_hash       TEXT NOT NULL
limitations_json           TEXT NOT NULL
error_code                 TEXT
error_message              TEXT
created_at                 INTEGER NOT NULL
started_at                 INTEGER
completed_at               INTEGER
```

Turn 已完成后，问题、范围、计划、答案、模型版本和 Snapshot 不允许 UPDATE。允许状态字段在合法状态机内变化。

#### `followup_retrieval_hits`

```text
turn_id                    TEXT NOT NULL
chunk_id                   TEXT NOT NULL
lexical_rank               INTEGER
vector_rank                INTEGER
rrf_score                  REAL
rerank_score               REAL
final_rank                 INTEGER NOT NULL
query_sources_json         TEXT NOT NULL
selected_for_context       INTEGER NOT NULL
PRIMARY KEY(turn_id, chunk_id)
```

#### `followup_citations`

```text
citation_id                TEXT PRIMARY KEY
turn_id                    TEXT NOT NULL
chunk_id                   TEXT NOT NULL
publication_number         TEXT NOT NULL
section_type               TEXT NOT NULL
section_label              TEXT NOT NULL
quote_text                 TEXT NOT NULL
quote_hash                 TEXT NOT NULL
start_offset               INTEGER
end_offset                 INTEGER
answer_path                TEXT NOT NULL
created_at                 INTEGER NOT NULL
```

`answer_path` 指向回答 JSON 中使用该引用的位置，例如：

```text
overlap_items[0].analysis
design_around_options[1].remaining_risks
```

#### `followup_tool_calls`

可以复用现有 `tool_calls`，但 `run_id` 仍指源 Run，并在 request summary 中记录 `thread_id/turn_id`。如果查询频率和展示需求增长，再拆独立表。

### 11.3 Variant 表

#### `idea_variants`

```text
variant_id                 TEXT PRIMARY KEY
case_id                    TEXT NOT NULL
source_run_id              TEXT NOT NULL
source_turn_id             TEXT
title                      TEXT NOT NULL
input_text                 TEXT NOT NULL
input_hash                 TEXT NOT NULL
change_set_json            TEXT NOT NULL
status                     DRAFT | SUBMITTED
child_run_id               TEXT
created_at                 INTEGER NOT NULL
submitted_at               INTEGER
```

Variant 默认是草稿。只有用户明确点击“重新评审”后，才创建新的不可变 child Run。

## 12. API 设计

本阶段所有内容读取接口属于公开共享空间。Case、Run、报告、Thread 和 Turn 的列表/详情接口不接收用户或租户范围参数，也不根据调用者过滤结果。证据范围校验仍然必须执行，因为它保证回答只引用源 Run 文献，但它不是访问控制。

### 12.1 查询可追问文献

```http
GET /api/idea/runs/{run_id}/followups/documents
```

返回：

- 深读文献；
- 语料可用状态；
- 文档版本；
- 可检索章节；
- 是否已有 embedding；
- 原文来源和抓取时间。

### 12.2 创建 Thread

```http
POST /api/idea/runs/{run_id}/followups/threads
```

请求：

```json
{
  "title": "与 CN... 的重合分析",
  "default_mode": "EVIDENCE_QA",
  "publication_numbers": ["CN..."]
}
```

创建时冻结允许的文档版本和 `corpus_snapshot_hash`。

### 12.3 提交一轮追问

```http
POST /api/idea/followups/threads/{thread_id}/turns
```

请求包含：

- `question`；
- 可选 mode；
- 可选本轮专利范围；
- 临时 Chat `base_url/api_key/model`。

查询 embedding 使用部署级 active profile，不接受请求临时覆盖，避免共享索引出现模型和维度碎片。

响应立即返回 Turn 状态，不等待完整模型回答。

### 12.4 事件流

```http
GET /api/idea/followups/turns/{turn_id}/events
```

事件至少包括：

- scope prepared；
- query planned；
- lexical/vector retrieval completed；
- rerank completed/degraded；
- answer generation started；
- citations verified；
- terminal。

事件和日志不得包含 API Key、完整向量或未截断的模型 Prompt。

### 12.5 获取回答和引用

```http
GET /api/idea/followups/threads/{thread_id}
GET /api/idea/followups/turns/{turn_id}
GET /api/idea/followups/citations/{citation_id}
```

Citation API 必须再次执行范围和哈希校验，并只返回必要上下文。

以下内容可以由任何访客读取：

```http
GET /api/idea/cases
GET /api/idea/cases/{case_id}
GET /api/idea/runs/{run_id}
GET /api/idea/runs/{run_id}/report
GET /api/idea/followups/threads
GET /api/idea/followups/threads/{thread_id}
```

通用公开 API 不提供物理删除、Corpus GC、全量对象导出或数据库维护能力。这些操作属于部署运维面，不设计用户组或站内管理员角色。

### 12.6 Variant 与新研究

```http
POST /api/idea/followups/turns/{turn_id}/variants
POST /api/idea/variants/{variant_id}/submit
POST /api/idea/followups/turns/{turn_id}/research-runs
```

所有创建新 Run 的接口都要求用户显式操作和新的临时模型凭证。

## 13. 配置设计

建议配置结构：

```json
{
  "access": {
    "mode": "public_shared",
    "show_public_submission_warning": true
  },
  "evaluation": {
    "analysis_evidence_mode": "hybrid_rag_v1",
    "force_abstract": true,
    "force_all_independent_claims": true,
    "expand_parent_claim_chain": true
  },
  "rag": {
    "retrieval": {
      "lexical_enabled": true,
      "vector_enabled": true,
      "lexical_top_k": 40,
      "vector_top_k": 40,
      "fusion_k": 60,
      "rerank_top_k": 24,
      "context_top_k": 12,
      "max_chunks_per_document": 6,
      "vector_backend": "pgvector"
    },
    "chunking": {
      "target_tokens": 1200,
      "max_tokens": 2000,
      "overlap_tokens": 150,
      "max_description_chunks": 200,
      "version": "patent-structure/1.0"
    },
    "embeddings": {
      "required_for_hybrid_mode": true,
      "allow_lexical_degraded": true,
      "active_profile": "patent-multilingual-v1",
      "credentials_source": "deployment_secret",
      "batch_size": 32
    }
  },
  "followup": {
    "enabled": true,
    "max_threads_per_run": 50,
    "max_turns_per_thread": 200,
    "recent_turns_in_context": 4,
    "max_context_characters": 60000,
    "citation_correction_attempts": 1
  },
  "database": {
    "backend": "postgresql",
    "url_env": "AIFPATENT_DATABASE_URL",
    "require_pgvector": true
  },
  "redis": {
    "url_env": "AIFPATENT_REDIS_URL",
    "required_in_production": true
  },
  "storage": {
    "object_backend": "s3",
    "bucket": "aifpatent-corpus",
    "prefix": "patent-corpus/"
  }
}
```

Pydantic Config、JSON Schema 和部署模板必须同步更新。数据库/Redis 凭证只能通过环境变量或 Secret 注入，不能写入 JSON。开发环境允许 `storage.object_backend=filesystem`，生产环境缺少 PostgreSQL、pgvector 或 Redis 时启动失败，不静默退回 SQLite/进程内协调。

## 14. 安全、隐私与数据授权

### 14.1 Prompt Injection

专利原文和网页内容一律视为不可信数据：

- 使用明确的数据分隔和 Citation Packet；
- System Prompt 声明专利文本不能提供指令；
- 不把 HTML script、隐藏文本或页面导航内容存入正文；
- Parser 只接受已知章节；
- 模型不能调用工具或扩大范围；
- 所有公开号和引用由后端白名单校验。

### 14.2 BYOK

- Chat 和 embedding Key 只在当前 Turn 运行内存使用；
- 不进入 Thread、Turn、向量 metadata、日志和错误消息；
- Base URL 和 Model 可以作为来源信息持久化；
- 重启后不得恢复凭证；
- 前端不得写 localStorage/sessionStorage。

### 14.3 公开数据与隐私边界

- 全站内容默认公开，首页、提交页和追问输入区必须明确提示这一点；
- Case、原始 IDEA、报告、专利范围、Thread 和回答都不能被提交者标记为“仅自己可见”；
- 系统暂不声称提供商业秘密保密空间，不应提交未公开发明细节、个人信息或受合同约束材料；
- 搜索引擎是否收录应由部署配置单独决定，但 `noindex` 不是权限控制；
- 匿名限流、风控和运维控制不改变内容公开属性；
- API Key、内部 Prompt、数据库凭证、对象存储签名和原始调试数据不属于公开内容。

### 14.4 数据授权

耐久保存和向量化前必须为每个 Provider 定义：

- 是否允许保存规范化全文；
- 是否允许长期缓存；
- 是否允许生成 embedding；
- 是否允许向第三方模型发送文本；
- 是否允许导出引用；
- 保留期限。

建议增加 `source_policy` 配置和 Version metadata。若某来源不允许耐久保存，只能标记 `REHYDRATABLE`，并在追问时按协议重新获取。

### 14.5 删除语义

- 删除 Thread：删除其 Turn、Retrieval Hit 和 Citation；
- 删除 Run：级联删除该 Run 的 Thread 和 Run-Version Link；
- 共享 Version/Blob 只有在没有任何 Run、Citation 或其他 Version 引用时才可进入显式 GC；
- 耐久 Corpus 不受 FIFO 自动清理；
- IDEA、对话和 Variant 的删除只通过受控运维操作执行，不向匿名访客开放任意删除；
- 专利公共语料是否保留由来源授权和 Corpus GC 策略决定。

## 15. 故障和降级语义

| 场景 | 行为 |
|---|---|
| 指定专利不属于源 Run | 422 范围错误，不调用模型 |
| Run 未完成 | 409，不允许创建 Thread |
| Blob 缺失但可重抓 | 尝试 rehydrate；失败则 limitation/失败 |
| Blob 哈希错误 | 标记 CORRUPT，禁止回答 |
| FTS 可用、embedding 不可用 | `LEXICAL_ONLY` 降级 |
| embedding 模型版本不一致 | 延迟重建或词法降级，不混用向量 |
| Reranker 不可用 | 使用 RRF 结果并记录 limitation |
| 无相关证据 | 返回 `INSUFFICIENT_EVIDENCE`，不编造答案 |
| 引用校验第一次失败 | 带合法别名清单纠错一次 |
| 引用再次失败 | Turn FAILED |
| 外部新检索必要 | 返回 `NEW_RESEARCH_REQUIRED`，等待用户确认 |
| 服务重启丢失 Key | Turn FAILED，保留历史 |

## 16. 性能和成本

### 16.1 一次入库，多次复用

- 规范化全文按内容哈希只保存一次；
- Chunk 在 Version 内生成一次；
- embedding 按文本哈希和模型版本只生成一次；
- 多个 Run 和 Thread 复用同一语料和向量；
- 每轮只对问题生成一个 query embedding；
- 模型只接收 Top-K Chunk，而不是全部全文。

### 16.2 延迟目标

建议开发验收目标，不作为外部 SLA：

- Scope/PostgreSQL FTS：P95 < 300 ms；
- Run 范围内 pgvector exact search：P95 < 500 ms；
- RRF + context assembly：P95 < 300 ms；
- 不含模型的检索总耗时：P95 < 1.5 s；
- 模型回答通过 SSE 流式展示阶段状态。

### 16.3 并发

- Web/API 与 Worker 可横向扩展，不能依赖进程内变量协调全局任务；
- Corpus ingest 使用 PostgreSQL 唯一约束、事务和 Redis `content_hash/version_id` 粒度租约保证幂等；
- 同一 Version 不重复分块和 embedding；
- 不同 Turn 可以并发；
- 每 Thread 同时只允许一个写入中的 Turn，避免对话分叉混乱；
- embedding 使用有界 batch/concurrency；
- Google Patents 请求按配置的“出口路由 + `patents.google.com`”在所有进程间全局并发为 1；Redis 锁必须覆盖等待、HTTP 请求、响应分类和下一次允许时间更新；
- 429 和风控页的冷却/熔断事实持久写入 PostgreSQL，Redis 缓存实时状态，服务重启或 Worker 切换不得立即重试；
- rehydrate 和首次全文抓取使用同一 Provider 队列、分布式限流和熔断；
- Follow-up 不能绕过 Provider 限流器直接抓取。

当前进程内 Google 锁只能保护单一事件循环，是迁移前兼容实现，不满足多 Worker 生产部署。

## 17. 可观测性

每个首次报告检索任务和 Follow-up Turn 至少记录：

- scope 中的 Version 数；
- FTS/vector/rerank 是否启用；
- 各阶段候选数量和耗时；
- Top-K Chunk 的 ID、排名和来源，不记录完整正文；
- 模型 input character/token estimate；
- 输出 token、响应 ID 和耗时；
- 引用数量、纠错次数和校验结果；
- limitation 和错误码；
- corpus snapshot、retriever、prompt 和模型版本。

健康检查增加：

- Corpus Store 可写/哈希 repair 状态；
- PostgreSQL、pgvector、FTS/`pg_trgm` 是否可用；
- Redis 队列、租约和 Provider 分布式限流器状态；
- embedding Provider 是否配置；
- VectorIndex 状态；
- 待 embedding/损坏 Version 数；
- Follow-up worker 状态。

不得把完整问题、答案、专利全文或密钥写入默认 JSONL 调试日志。产品 API 可以返回公开共享的业务内容，调试日志只记录摘要和哈希。

## 18. 前端信息架构

首页首先展示公开共享提示，并提供全站 Case、Run、报告和公开 Thread 列表。当前阶段不显示登录、团队切换、私有空间或“仅自己可见”控件。

新生成的报告页必须展示可展开的首次评审 Citation，并标记本 Run 使用的证据模式：`LEGACY_PACKET`、`LEXICAL_RAG` 或 `HYBRID_RAG`。历史报告保持原样。

报告页增加“基于本次评审追问”区域：

```text
左侧：Thread 列表
中间：对话和建议问题
右侧：本轮证据、专利范围、原文定位和限制
```

交互要求：

- 从某篇专利卡片发起时默认只选择该专利；
- 用户可以切换为多篇比较或全部深读专利；
- 当前范围始终可见；
- 引用以 `公开号 · Claim/段落` Chip 展示；
- 点击 Citation 展开原文，不跳转也能核验；
- 设计规避建议单独卡片展示工程代价和剩余风险；
- “创建 IDEA 变体”和“发起新检索”必须是显式按钮；
- 新研究产生的新 Run 与原 Thread 双向关联；
- 失败和降级显示真实错误，不伪造回答。

建议预置问题：

- “这篇专利与我的 IDEA 重合点集中在哪里？”
- “请按 F1..Fn 对照独立权利要求。”
- “哪些区别特征最可能支撑新颖性？”
- “给出三种保留技术效果的替代实现。”
- “这些替代实现还需要检索什么？”

## 19. 测试与完成门槛

### 19.1 Corpus 测试

- 同内容重复入库只产生一个 Blob/Version；
- 仅改变 `retrieved_at`、来源 URL 或任务 ID 不产生新内容版本；
- 内容变化创建新 Version，不覆盖旧版本；
- 多 Run 能冻结引用不同版本；
- gzip/zstd 解压和 SHA-256 校验；
- 对象上传或 PostgreSQL 事务中断不会产生 READY 的坏记录；
- Corpus 不受 FIFO 清理；
- 删除一个 Run 不删除仍被其他 Run 引用的 Blob；
- parser/chunker 升级的版本语义正确；
- 历史 Run rehydrate 成功/失败语义正确。

### 19.2 Retrieval 测试

- PostgreSQL `tsquery` 正确生成并参数化，中文术语、英文术语、公开号和缩写均有 fixture；
- 范围过滤不能返回其他 Run 文献；
- pgvector exact cosine 与固定向量 fixture 一致；
- embedding 维度/模型不一致时 fail closed；
- RRF 排名确定性；
- 章节权重不突破范围过滤；
- 相同文本和滑窗结果正确去重；
- 单篇、多篇比较的证据配额；
- embedding/reranker 不可用时按设计降级。
- 每篇深读专利的所有独立权利要求都被评估；
- 从属权利要求命中时正确补齐父权利要求链；
- `NOT_DISCLOSED` 不会仅因普通 Top-K 未命中而产生。

### 19.3 Answer 测试

- 未知 Citation alias 被拒绝；
- 跨文献 Citation 被拒绝；
- quote/hash 不一致被拒绝；
- 未知公开号被拒绝；
- `HIGH` 重合无引用被拒绝；
- 技术重合不会生成确定侵权结论；
- 证据不足返回 `INSUFFICIENT_EVIDENCE`；
- Variant 不会自动修改原 Run；
- 回答只使用本轮 Packet；
- 模型纠错最多一次。
- 新 Run 报告的 Evidence 能定位到冻结的 `version_id/chunk_id`；
- 确定性新颖性判断不跨文献拼接 Chunk。

### 19.4 API/Workflow 测试

- 未完成 Run 无法创建 Thread；
- Thread Snapshot 创建后不可变；
- Turn 合法状态转换、重试、取消和重启；
- BYOK 不进入 PostgreSQL、Redis、对象存储、Checkpoint、日志和响应；
- SSE 能在刷新后恢复状态；
- 原 Run、报告和 Manifest 在追问后哈希不变；
- 删除 Thread/Run 的引用和文件语义正确。
- 未登录访客能列出和读取全部 Case、Run、报告、Thread、Turn 和 Citation；
- 公开 UI/API 不暴露数据库维护、Corpus GC 或任意删除入口；
- 多 Worker 并发时 Google Provider 的网络临界区仍全局为 1，熔断在重启后有效。

### 19.5 真实验收集

至少准备：

- 5 个中文 IDEA；
- 每个 IDEA 10～20 篇深读专利；
- 中文、英文和中英交叉问题；
- 单篇重合解释；
- 两篇权利要求比较；
- 三个设计规避问题；
- 两个必须回答证据不足的问题；
- 两个需要升级新检索的问题；
- 人工核对 Citation 是否真的支持回答。

关键指标：

- Citation precision；
- Citation coverage；
- Scope violation rate 必须为 0；
- Unknown publication rate 必须为 0；
- Corpus dedup ratio；
- 每 Turn 输入 token 和费用；
- 人工判断回答是否区分事实、推理和建议。

## 20. 分阶段实施计划

### Phase 0：目标基础设施与迁移基线

- 固定本文档；
- 建立 PostgreSQL/pgvector、Redis、MinIO 的开发部署模板和健康检查；
- 定义 Repository、ObjectStore、JobQueue、DistributedLimiter 和 VectorIndex 接口；
- 为现有 SQLite 数据生成只读导出、PostgreSQL 导入和校验样本；
- 决定 embedding Provider 配置方式；
- 明确各数据源全文留存授权；
- 固定 `public_shared` 访问模式和公开提交警告；
- 增加 Corpus、首次报告 RAG 和追问的独立 feature flag，默认关闭。

完成门槛：本地可一键启动目标依赖，迁移演练不会修改原 SQLite，且 PostgreSQL 导入后的 Case/Run/报告与源数据哈希核对通过。

### Phase 1：PostgreSQL、对象存储与分布式协调

建议 Work Unit：

1. `IDEA-PG-CORE-001`：核心业务 Schema、Repository 和 SQLite→PostgreSQL 迁移；
2. `IDEA-OBJECT-STORE-001`：本地/S3/MinIO ObjectStore、条件写和 repair；
3. `IDEA-REDIS-JOBS-001`：异步 Job、幂等租约、取消和恢复；
4. `IDEA-PROVIDER-LIMITER-001`：Google 跨进程串行限流与持久熔断；
5. `IDEA-PUBLIC-ACCESS-001`：公开列表/详情与隐私提示，禁止公开破坏性运维入口。

完成门槛：两个 Worker 并发运行时业务写入一致，Google 网络请求全局串行，服务重启后任务与熔断语义正确，任何访客可读取全部业务内容但看不到凭证和运维操作。

### Phase 2：耐久 Corpus

建议 Work Unit：

1. `IDEA-CORPUS-SCHEMA-001`：Version/Blob/Run Link Schema；
2. `IDEA-CORPUS-STORE-001`：稳定内容哈希、压缩、对象写入和 repair；
3. `IDEA-CORPUS-INGEST-001`：接入 Fetch，先入库再释放全文；
4. `IDEA-CORPUS-CHUNK-001`：专利结构化分块；
5. `IDEA-CORPUS-MIGRATE-001`：历史 Run rehydrate 工具。

完成门槛：新 Run 深读文献全部有 READY Version，且 FIFO 清理后仍能读取全文。

### Phase 3：共享词法 RAG 与首次报告改造

1. `IDEA-RAG-PGFTS-001`：PostgreSQL FTS/`pg_trgm`、中英 tokenization 和 repair；
2. `IDEA-REPORT-RETRIEVAL-001`：`F_i × D_j` 检索命中与审计表；
3. `IDEA-REPORT-CLAIMS-001`：摘要、独立权利要求强制覆盖和父权利要求链；
4. `IDEA-REPORT-RAG-001`：`ANALYZE_DOCUMENTS` 使用共享 Retriever/ContextAssembler；
5. `IDEA-REPORT-CITATION-001`：报告 Evidence 绑定 Version/Chunk 并在 UI 展开原文。

完成门槛：新 Run 的首次报告不再依赖旧式说明书片段 Top-K 作为唯一输入；所有独立权利要求均被评估，报告 Evidence 能定位到耐久原文，并明确标记 `LEXICAL_RAG`。

### Phase 4：pgvector 混合检索与追问 MVP

1. `IDEA-EMBED-001`：EmbeddingProvider 和缓存；
2. `IDEA-VECTOR-001`：`PgVectorIndex` 精确检索与索引基准；
3. `IDEA-RAG-HYBRID-001`：RRF、章节权重和多样性；
4. `IDEA-FOLLOWUP-DB-001`：Thread/Turn/Retrieval/Citation Schema；
5. `IDEA-FOLLOWUP-WF-001`：独立固定追问 Workflow；
6. `IDEA-FOLLOWUP-ANSWER-001`：结构化回答和引用门禁；
7. `IDEA-FOLLOWUP-API-001`：公开 API、SSE、取消和 BYOK；
8. `IDEA-FOLLOWUP-UI-001`：Thread、范围和引用展开；
9. `IDEA-RERANK-001`：可选语义重排；
10. `IDEA-RAG-EVAL-001`：首次报告与追问共用的离线检索/引用评测集。

完成门槛：首次报告和追问都使用同一混合 Retriever；相较纯词法检索提升相关证据召回率，且不增加范围越界；FIFO 清空后仍能对指定深读文献稳定追问。

### Phase 5：Variant 和新研究

1. `IDEA-VARIANT-001`：变体草稿和差异集；
2. `IDEA-VARIANT-RUN-001`：用户确认后创建 child Run；
3. `IDEA-FOLLOWUP-RESEARCH-001`：区别特征二次检索；
4. `IDEA-CLAIM-CHART-001`：权利要求对照表；
5. `IDEA-LEGAL-DATA-001`：同族、法律状态和有效权利要求来源。

## 21. 已固定的架构决策

后续实现默认遵守以下决策；若需改变，应新增 ADR 或修订本文档版本：

1. 外部查询规划与候选搜索继续负责广域召回，RAG 从深读文献全文获取后开始；
2. 首次报告和追问共用 Version、Chunk、Retriever 与 Citation 基础设施；
3. 追问是原 Run 的派生子系统，不修改固定 11 步 Workflow；
4. 已生成的原报告、结论和 Manifest 不可变；
5. 只对深度分析文献及显式新研究入选文献默认持久化全文，普通候选只保存元数据；
6. 追问范围默认只包含源 Run 的深读文献版本；
7. 新外部检索必须创建显式研究任务或 child Run；
8. 专利身份与内容版本分离，正文哈希排除抓取时间等易变来源字段；
9. 全文使用内容寻址压缩对象并保存到 S3/MinIO 兼容 ObjectStore，不位于 FIFO Cache；
10. Run 冻结绑定具体文档版本；
11. Chunk 保留权利要求和说明书结构，不只按固定 token 切分；
12. PostgreSQL 是目标业务事实来源，PostgreSQL FTS/`pg_trgm` 是词法索引；
13. pgvector 是首个生产向量实现，小范围优先精确检索；
14. Redis 负责 Job、分布式锁和跨进程实时限流，持久熔断事实进入 PostgreSQL；
15. 当前访问模式为 `public_shared`，不建设用户、组织、分组、租户隔离或内容 ACL；
16. 混合排序使用 RRF，不直接混加不可比分数；
17. 首次报告强制评估摘要和所有独立权利要求，并对命中从属权利要求补齐父链；
18. 每轮追问重新检索证据，聊天历史不是专利事实来源；
19. 模型只引用当前 Citation Packet 的别名；
20. 引用、公开号、范围和原文哈希由程序确定性校验；
21. 没有证据必须明确返回不足，不允许依赖模型记忆；
22. 新颖性不得跨文献拼接，技术重合、现有技术风险、权利要求相关性和法律侵权严格区分；
23. Design-around 只生成候选，正式评审必须创建 Variant/子 Run；
24. BYOK 凭证不持久化，服务重启后不能秘密恢复；
25. 数据源授权不允许的全文不得以技术手段绕过限制持久化。

## 22. 推荐的首个开发切片

第一批代码不要直接从聊天 UI 开始。推荐最小垂直顺序：

```text
PostgreSQL/pgvector + Redis + ObjectStore
→ SQLite 数据迁移与核验
→ Corpus Schema
→ Content-addressed Object Store
→ Fetch 后幂等 Ingest
→ Run-Version Freeze
→ Patent-aware Chunking
→ PostgreSQL FTS 查询 CLI/测试接口
→ 新 Run 的 Feature × Patent 证据检索
→ 报告 Citation 展开
→ 一轮无历史的 Evidence QA
→ Citation Verifier
→ Thread/Turn API
→ UI
```

第一个可验收演示应是：

1. 在目标 PostgreSQL/Redis/ObjectStore 环境完成一个 quick Run；
2. 证明普通候选只保存元数据，深读专利全文已形成 READY Version；
3. 新报告对全部独立权利要求完成评估，并展示可展开的 F1..Fn Citation；
4. 清空 FIFO Cache 后，报告 Citation 仍能读取并通过原文哈希校验；
5. 选择其中一篇深读专利并提问“该专利与 IDEA 的重合点在哪里”；
6. 系统复用同一 Version/Chunk，从耐久 Corpus 检索 Claim/Description；
7. 返回带 Citation 的回答；
8. 证明追问没有修改该 Run 已生成的报告和 Manifest；
9. 用两个 Worker 验证 Google 网络请求仍全局串行；
10. 以未登录浏览器验证全部 Case/Run/报告/Thread 可见且 BYOK 不可见。

首个切片允许先以 `LEXICAL_RAG` 验收数据闭环，再加入 pgvector、RRF 和 Reranker；但所有接口和审计字段从一开始按共享 Retriever 设计，不能为首次报告和追问各写一套不可复用实现。

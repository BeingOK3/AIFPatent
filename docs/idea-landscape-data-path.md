# IDEA 检索与专利洞察：一次数据从输入到报告的完整路径

本文用一个虚构但贴近产品的例子，说明 AIFPatent 两个板块实际怎样使用数据：

- **IDEA 检索/评审**：回答「我的方案是否可能被现有专利披露？」
- **专利洞察（Landscape）**：回答「这个技术方向里有哪些公司、各自在做什么、整体趋势如何？」

示例技术主题是：**用于 AI 服务器的液冷回路：按温度传感器反馈调节冷却液流量，并检测冷却液劣化。**

文中的 `IDEA-R1`、`LAND-R1`、`CN123456A` 和哈希值均为便于讲解而虚构的示例 ID；表名、字段名、步骤名和存储边界均对应当前代码。

## 先建立一个正确的心智模型

系统有三类持久化位置，职责不同：

| 位置 | 可以把它想成 | 在这里负责什么 | 不能把它当成什么 |
| --- | --- | --- | --- |
| PostgreSQL | 有关系和索引的档案馆 | 任务、专利身份、版本目录、Chunk、检索命中、证据、公司分析 | 大文件仓库 |
| MinIO（S3 接口） | 按编号取件的不可变文件仓库 | IDEA 语料的完整规范化专利正文 Blob | 能按「液冷」等字段检索的数据库 |
| App 工作区/缓存 | 临时工位 | 导出报告、缓存、运行日志 | 可作为长期事实来源的数据库 |

所谓对象存储中的“对象”，本质是 `key → bytes`。例如：

```text
key: patent-corpus/CN123456A/ab12...ef.json
bytes: {"publication_number":"CN123456A","claims":[...],"description":"..."}
```

MinIO 知道如何通过 key 取回这串 bytes，却不知道 JSON 内的“权利要求 1”是什么意思。PostgreSQL 则保存对象的哈希、key、版本关系和从正文拆出的可检索片段。两者配合，既避免大正文和业务关系混杂，又能把每条结论追溯回原文。

> 重要现状：当前代码中，**IDEA 的耐久 Corpus 正文进入 MinIO**；Landscape 当前将本轮抓取的详情 JSON 保存到 `landscape_document_fetches.document_json`（PostgreSQL），并不自动把它转写为 IDEA 的 MinIO Corpus。两条链路复用相同的 PostgreSQL 部署，但数据模型和运行编号相互独立。

---

## 场景 A：IDEA 检索/评审的数据路径

### 用户动作和最终结果

用户创建 IDEA Case「AI 服务器自适应液冷」，输入方案：

> 根据多个温度传感器控制泵速；当电导率或光学传感器表明冷却液劣化时触发告警并调整流量。

系统最终给出一份新颖性/创造性报告，其中可能包含：

```text
特征 F-03：基于冷却液劣化检测来调整流量
结论：PARTIAL（部分披露）
证据：C7，CN123456A，权利要求 1，第 120–248 字符
```

这行结论不是只存一段模型文本；下面的链路记录了它从何而来。

### 全链路图

```text
浏览器输入方案
  │
  ▼
IDEA-R1 / 11 步工作流 ────────→ PostgreSQL：运行、步骤、模型配置快照
  │
  ├─ 解析出特征 F-01..F-03 ───→ PostgreSQL：idea_features
  ├─ 生成查询、调用检索服务 ──→ PostgreSQL：search_queries / search_hits / tool_calls
  │
  ▼
抓取 CN123456A 的完整专利
  │
  ├─ 规范化、计算 SHA-256
  ├─ 写完整 JSON ─────────────→ MinIO：patent-corpus/CN123456A/<hash>.json
  └─ 写目录与版本 ────────────→ PostgreSQL：documents / versions / blobs / sources
  │
  ▼
将正文拆成摘要、权利要求、说明书 Chunk
  └──────────────────────────→ PostgreSQL：patent_chunks + 全文/可选向量索引
  │
  ▼
F-03 检索命中 Chunk，构造 C7 上下文
  └──────────────────────────→ PostgreSQL：retrieval queries/hits/context/citations
  │
  ▼
输出报告和可回查引用
```

### 1. 创建 Run：先固定“本次评审到底按什么条件运行”

用户点击开始后，系统创建 `idea_cases` 中的 Case 和 `idea_runs` 中的 `IDEA-R1`。`idea_runs` 会保存状态、模型名、工作流版本、评审日期、范围及 `config_snapshot`。输入正文放在 `run_inputs`；11 个固定步骤的状态、输入/输出哈希和结果放在 `run_steps`、`stage_results`。

IDEA 的步骤依次是：

```text
PREPARE_INPUT → PARSE_IDEA → VALIDATE_IDEA_MODEL → PLAN_QUERIES
→ RETRIEVE_CANDIDATES → NORMALIZE_AND_FETCH → ANALYZE_DOCUMENTS
→ DETERMINE_NOVELTY → ANALYZE_INVENTIVENESS → ASSESS_VALUE → AUDIT_AND_REPORT
```

因此，重试或审计时，系统能区分“原始输入是什么”“当时调用的模型和配置是什么”“哪个步骤失败/重做过”，而不是仅保留最后一份报告。

### 2. 从方案文本变成可检索的技术特征

`PARSE_IDEA` 把用户的一段描述拆成有顺序的 Feature。例如：

| `idea_features` 记录 | `ordinal` | `feature_text` |
| --- | ---: | --- |
| F-01 | 1 | 多个温度传感器采集服务器冷却状态 |
| F-02 | 2 | 基于温度反馈调节泵速/冷却液流量 |
| F-03 | 3 | 依据电导率或光学检测到的冷却液劣化作出控制/告警 |

随后 `PLAN_QUERIES` 生成中英文等查询词。每条查询写入 `search_queries`；外部 Provider 返回的候选写入 `search_hits`，原始 Provider 响应进入 `raw_json`，同时也会留下 `tool_calls` 审计记录。

这层数据的意义是：将来可以回答“CN123456A 是被哪条检索词找到的”，而不仅是“它出现在结果列表中”。

### 3. 一篇候选专利如何同时进入 PostgreSQL 和 MinIO

假设 `NORMALIZE_AND_FETCH` 抓到了 `CN123456A`。处理后有两份互补数据：

```text
PostgreSQL（能关联、筛选、检索）       MinIO（完整、不可变正文）
─────────────────────────────         ───────────────────────────────
patent_documents                        patent-corpus/CN123456A/
  document_id = DOC-CN123456A              ab12...ef.json
  publication_number = CN123456A                └─ 完整规范化 JSON bytes
  title / assignee / 日期等

patent_document_versions
  version_id = CV-...
  normalized_content_hash = ab12...ef
  normalized_blob_hash = ab12...ef

corpus_blobs
  blob_hash = ab12...ef
  object_key = patent-corpus/CN123456A/ab12...ef.json
  state = READY
```

实际写入顺序是**先对象可读，再提交数据库版本记录**。`PatentCorpusIngestService.ingest()` 先对规范化 JSON 计算 SHA-256，用哈希生成对象 key，再调用 `put_if_absent`。同 key 的内容已存在且哈希一致时复用；不一致时抛错，拒绝覆盖。对象写入和读取都会校验哈希。

这就是“版本不可变”的实际含义：以后同一公开号抓到内容不同的正文，不会覆盖 `ab12...ef.json`，而是生成另一个 hash、一个新的 `patent_document_versions` 记录。`run_document_versions` 再将 `IDEA-R1` 锁定到它当时采用的 `CV-...`，保证日后重新查看报告时不会误用更新后的全文。

### 4. 从整篇正文到可回答问题的证据段

整篇专利适合归档，却不适合直接检索。因此系统把冻结版本拆为 `patent_chunks`：

| `chunk_id` | 内容 | 用途 |
| --- | --- | --- |
| CH-001 | 摘要 | 快速判断技术方向 |
| CH-002 | 权利要求 1 | 判断核心方案是否披露 |
| CH-003 | 权利要求 2 | 补充限制条件 |
| CH-018 | 说明书段落 | 获取实现细节 |

每个 Chunk 都带 `version_id`、章节类型、权利要求编号、父权利要求、原文偏移量、文本哈希和 token 数。PostgreSQL 为其建立 `search_tsv` 全文索引和 `pg_trgm` 模糊索引；如果配置了 Embedding，还会通过 `embedding_vectors`/`chunk_embeddings` 加入向量检索。

这里也解释了为什么不能只把 PDF/JSON 放进 MinIO：MinIO 可以取回全文，但它无法高效回答“找出所有含劣化检测且属于独立权利要求的段落”。这个问题由 `patent_chunks` 和 PostgreSQL 索引完成。

### 5. 从检索命中到报告中的 C7

对 `F-03`，系统在**本次 Run 已冻结的版本范围内**检索，记录：

```text
report_retrieval_queries
  IDEA-R1 + F-03 + CV-... + 查询文本

report_retrieval_hits
  IDEA-R1 + F-03 + CH-002
  selection_reason = lexical（或 vector/rerank/forced_claim）
  selected_for_context = true
```

选中的 Chunk 进入确定性的 Context Manifest，被分配临时别名如 `C7`。模型只能基于这些上下文完成判断。最后，`report_model_citations` 保存：`IDEA-R1` 的某项 Feature、`C7`、`CH-002` 和 `CN123456A` 的关联。

所以用户点击 C7 时，系统的回查路径是：

```text
C7 → report_model_citations → CH-002 → CV-... → corpus_blobs.object_key
   → MinIO 中 CN123456A 的完整冻结正文
```

报告 JSON/Markdown 及 manifest 会登记在 `reports`/`artifacts` 中；文件本体属于应用工作区产物，而不是 MinIO Corpus 的替代品。

---

## 场景 B：专利洞察（Landscape）的数据路径

### 用户动作和最终结果

用户在专利洞察页面输入：

```text
技术方向：AI 服务器液冷与冷却液状态监测
时间范围：2024-01-01 至 2026-06-30
模式：TECHNOLOGY_COMPETITOR
重点公司：Huawei、Vertiv、Meta
```

系统创建 `LAND-R1`，最终输出公司分布、技术类别、每家公司技术画像、跨公司趋势，以及相关专利清单/报告。

它和 IDEA 的区别是：IDEA 围绕“一个用户方案的 Feature 是否被披露”；Landscape 围绕“一个技术主题下的候选集合、公司归属和横向比较”。因此它有自己的 `landscape_*` 表，不能把 IDEA 的 `IDEA-R1` 当作它的 Run。

### 全链路图

```text
浏览器输入范围、方向、公司
  │
  ▼
LAND-R1 ──────────────────────→ PostgreSQL：landscape_runs / steps / stage_results
  │
  ├─ 搜索规划和 Provider 返回 ─→ landscape_queries / landscape_hits
  ├─ 去重、时间/公司过滤、排序 ─→ landscape_candidates
  ├─ 抓详情（本轮 JSON） ───────→ landscape_document_fetches.document_json
  ├─ 分配公司和技术类别 ────────→ companies / document_companies / categories
  ├─ 公司画像与趋势 ────────────→ profiles / cross_company_trends
  └─ 可回查证据与报告 ──────────→ evidence / insight_evidence / landscape_reports
```

### 1. 固定范围，生成搜索计划

`landscape_runs` 保存 `LAND-R1` 的模式、起止公开日、用户范围、模型/Prompt/工作流版本、输入哈希和配置快照。`landscape_steps`、`landscape_stage_results` 保存每一步状态和结构化结果。

主流程步骤为：

```text
VALIDATE_SCOPE → PLAN_SEARCH → SEARCH_PUBLICATIONS → FILTER_AND_SELECT
→ FETCH_DETAILS → ANALYZE_PATENTS（当前主流程为可选延后精读）
→ ANALYZE_COMPANIES → ANALYZE_CROSS_COMPANY_TRENDS
→ VERIFY_COVERAGE → BUILD_REPORT
```

若覆盖率检查发现缺口，工作流可进入 `REPAIR_GAPS` 再检查。这样“范围、候选、分析、覆盖检查”是可恢复的独立阶段，不是一段一次性模型对话。

`PLAN_SEARCH` 例如生成“server liquid cooling flow control”“coolant conductivity degradation monitoring”等查询，并写入 `landscape_queries`。Provider 的每个结果都写到 `landscape_hits`，包括 provider、命中的公开号、申请人、日期、原始 JSON，以及最终 `ELIGIBLE` 或 `EXCLUDED` 决定和排除原因。

### 2. 从搜索结果变成可分析的“本轮候选池”

`FILTER_AND_SELECT` 对候选按公开号/同族等进行规范化、去重，并执行用户指定的时间范围、技术相关性、公司范围等硬条件。通过的候选写入 `landscape_candidates`：

```text
LAND-R1 + DOC-CN123456A
  publication_number = CN123456A
  rank = 4
  decision = ELIGIBLE
  metadata_json = 标题、日期、申请人、同族、来源查询、排序原因
```

这张表是本轮 Landscape 的边界：后续公司分析、证据、分类必须引用这里的候选，而不能悄悄混入搜索外的专利。

### 3. 详情抓取目前存在哪里？

对每个合格候选，`FETCH_DETAILS` 向 Provider 获取完整详情。当前 Landscape 的耐久抓取状态保存在：

```text
landscape_document_fetches
  (run_id, document_id, publication_number)
  status = FETCHED | FAILED
  document_json = 本轮抓取到的详情 JSON
  content_hash、attempt_count、失败原因
```

这使得中断后能恢复，且能区分“未抓”“抓取成功”“抓取失败”。注意这里是当前实现的边界：它是 PostgreSQL JSONB，不等同于 IDEA 那套 `corpus_blobs → MinIO` 不可变语料管道。

### 4. 候选专利如何变成“哪个公司在做什么”

对候选池，系统先把申请人名称归一化。例如 “Huawei Technologies Co., Ltd.”、不同语言拼写或别名，最终归到同一个 `company_id`。数据分别落在：

| 表 | 保存内容 |
| --- | --- |
| `landscape_companies` | 公司规范名、别名、归一化来源、置信度 |
| `landscape_document_companies` | 某件专利属于哪家公司、PRIMARY/CO_ASSIGNEE、原始申请人、置信度 |
| `landscape_company_assignment_manifests` | 本轮公司/分配总数及内容哈希，用于冻结分配集合 |

例子：`CN123456A` 被分配给 `CO-HUAWEI`；另一件 `US...` 属于 `CO-VERTIV`。接下来系统按公司构造输入，提取或使用轻量技术指纹，将专利分到“流量自适应控制”“冷却液状态监测”等分类。

分类结果写入 `landscape_company_categories`，每个分类与具体专利的对应关系在 `landscape_company_category_members`；公司的总结、技术方向和限制条件写入 `landscape_company_profiles`。

### 5. 趋势、证据、报告

`ANALYZE_CROSS_COMPANY_TRENDS` 对已冻结的公司画像/候选集合做横向分析，结果在 `landscape_cross_company_trends`。趋势结论不会只存自由文本：它通过 `landscape_insight_evidence` 关联到 `landscape_evidence`，后者包含公开号、原文摘录、位置范围和内容哈希。

例如：

```text
趋势 T-02：多家公司从固定阈值控制转向传感器反馈的动态流量控制
  ↓ evidence link
LAND-R1 / CN123456A / 权利要求 1 的摘录
LAND-R1 / US... / 说明书第 18 段的摘录
```

最后报告和专利 CSV 的路径、哈希、manifest 记录在 `landscape_reports`。因此一个 Landscape 结论可沿着“趋势 → 证据 → 本轮候选 → 抓取详情/来源命中”回查。

---

## 两条路径的相同点、不同点和交会处

| 维度 | IDEA 检索/评审 | 专利洞察（Landscape） |
| --- | --- | --- |
| 业务问题 | 一个方案的技术特征是否被披露 | 一个方向和公司组合的格局/趋势 |
| 主 Run 表 | `idea_runs` | `landscape_runs` |
| 检索审计 | `search_queries`、`search_hits` | `landscape_queries`、`landscape_hits` |
| 正文耐久化 | 当前使用版本化 Corpus：PostgreSQL 元数据 + MinIO Blob | 当前抓取详情存 `landscape_document_fetches.document_json` |
| 可检索单元 | `patent_chunks`，用于 Feature × Patent 证据检索 | 本轮候选、指纹、公司类别、可选精读分析 |
| 最终引用 | `report_model_citations` 指向 Chunk | `landscape_insight_evidence` 指向 Landscape Evidence |
| 共同基础设施 | 同一 PostgreSQL、Provider、应用工作区和部署栈 | 同左 |

这里最值得注意的是：**两条产品链路都追求可回查，但它们不是同一张大表。** 分开建模避免将 IDEA 的“专利版本/Chunk/RAG 证据”与 Landscape 的“候选池/公司归属/趋势”混成难以维护的万能 JSON；共享基础设施则减少部署和运维成本。

## 读代码时的推荐顺序

1. IDEA 的流程枚举：[backend/idea/workflow.py](../backend/idea/workflow.py)。
2. IDEA 全文写对象存储和版本记录：[backend/idea/corpus.py](../backend/idea/corpus.py)；S3/MinIO 的防覆盖和校验：[backend/idea/s3_object_store.py](../backend/idea/s3_object_store.py)。
3. IDEA Corpus、Chunk、检索表：[deploy/rag/postgres-init/020_corpus_schema.sql](../deploy/rag/postgres-init/020_corpus_schema.sql)。
4. Landscape 流程枚举：[backend/landscape/workflow.py](../backend/landscape/workflow.py)；各阶段实现：[backend/landscape/execution.py](../backend/landscape/execution.py)。
5. Landscape 的基础运行表：[deploy/rag/postgres-init/060_unified_runtime_schema.sql](../deploy/rag/postgres-init/060_unified_runtime_schema.sql)，公司与趋势表：[deploy/rag/postgres-init/070_landscape_company_analysis.sql](../deploy/rag/postgres-init/070_landscape_company_analysis.sql)。

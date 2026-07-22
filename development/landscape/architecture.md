# 专利态势分析 MVP 架构

状态：`APPROVED_FOR_IMPLEMENTATION`

日期：2026-07-22

## 1. 目标

为内部研究人员提供独立页面，分析：

1. 一个具体技术方向在指定公开日期窗口内的新公开专利；或
2. 一个或多个重点友商在指定公开日期窗口内的新公开专利。

输出同时包含确定性统计和逐件模型精读：

- 筛选后专利的申请日趋势；
- 公开号法域分布和已确认同族法域分布；
- 技术聚类；
- 每件专利的申请号、名称、申请日、当前权利人、同族布局；
- 现有技术、现有技术问题、核心技术发明点、解决的技术问题和有益效果；
- 检索覆盖、排除原因、数据缺失和失败限制。

## 2. 输入语义

### 2.1 模式

- `TECHNOLOGY`：技术方向必填，重点友商可选；友商只高亮和对比，不过滤其他申请人。
- `COMPETITOR`：重点友商必填，技术方向可选；友商别名用于严格后过滤。

同时填写技术方向和友商时不得隐式改变模式，避免无意形成过窄 AND 查询。

### 2.2 时间

“新公开”以公开日为硬条件，窗口两端均包含：

```text
publication_start <= publication_date <= publication_end
```

预设支持 1 个月、1 个季度、6 个月、12 个月和自定义日期，MVP 最长 12 个月。报告中的“申请日趋势”只统计已经通过公开日过滤的文献之 `filing_date`，不得把申请日当作检索窗口。

### 2.3 友商别名

页面按行输入友商，使用 `|` 分隔经过用户确认的别名，例如：

```text
华为 | Huawei | Huawei Technologies
三星 | Samsung | Samsung Electronics
```

模型可以生成检索词，但不能把未经用户确认的公司别名加入严格过滤集合。

### 2.4 预算

每次 Run 保存不可变预算：

- `candidate_limit`：最多保留多少严格过滤后的候选，MVP 为 10～200；
- `analysis_limit`：最多抓取并逐件精读多少件，MVP 为 1～50；
- `per_query_limit`：单查询 Provider 上限；
- `document_concurrency`：受配置控制，不由模型决定。

只要候选数量超过精读上限，报告必须显示截断数量和停止原因，不得把精读子集描述为完整结果集。

## 3. 冻结边界

本功能不修改下列 IDEA 业务资产：

- `backend/idea/workflow.py` 与固定 11 步；
- `backend/idea/execution.py`；
- `backend/idea/followup_*`；
- IDEA/Follow-up 数据表、Prompt、报告 Schema 和历史文件；
- `frontend/app.js` 的 IDEA/追问状态机。

共享入口只允许：

- 在 `backend/main.py` 装配新 Router、启动恢复和关闭钩子；
- 为 `/landscape` 返回独立 HTML；
- Docker 继续复制整个 `backend/` 和 `frontend/`，不新增镜像依赖时无需改变容器定义。

## 4. 代码边界

```text
backend/landscape/
  schemas.py          领域枚举、请求、模型输出和报告契约
  database.py         独立 SQLite schema/repository
  store.py            不可变输入、报告和 manifest
  planning.py         技术方向查询规划
  search.py           Provider 调用、严格过滤、去重和覆盖统计
  analysis.py         全文抓取、直接证据包和逐件精读
  clustering.py       无向量的结构化主题聚类
  reporting.py        确定性统计、JSON/Markdown/CSV
  workflow.py         固定 LangGraph 和步骤状态
  execution.py        节点业务编排和文献临时生命周期
  runtime.py          共享 Provider/Model 的只读装配
  api.py              Run、SSE、取消、报告和 BYOK 生命周期

frontend/
  landscape.html
  landscape.js
  landscape.css
```

## 5. 运行链路

```text
VALIDATE_SCOPE
  -> PLAN_SEARCH
  -> SEARCH_PUBLICATIONS
  -> FILTER_AND_SELECT
  -> FETCH_DETAILS
  -> ANALYZE_PATENTS
  -> CLUSTER_PATENTS
  -> BUILD_REPORT
```

控制流固定，模型不能添加、跳过或重排节点。Checkpoint 只保存 Run ID 和步骤坐标，不保存 API Key、专利全文或模型输入。

### 5.1 查询规划

新增严格 `LandscapeQueryPlan`：

- 2～6 个中英文查询；
- 可审计的方向术语；
- 每条 query 的语言和理由；
- 不包含 Provider Token、日期结论或搜索结果断言。

友商别名、公开日窗口和预算始终由程序追加或作为 Provider 参数传入，模型不能改写。

### 5.2 搜索和过滤

复用现有 `SearchProvider`、`ProviderRunner`、`GooglePatentsProvider`、`ExaMcpProvider` 和 `merge_hits`，但在新业务服务中建立独立规则：

1. Provider 侧尽量加入公开日/申请人条件；
2. 返回后再次解析 ISO 公开日并做包含式硬过滤；
3. `COMPETITOR` 模式按用户确认别名匹配申请人字段；
4. 公开号无效、公开日缺失或超窗不得进入正式结果；
5. 公开号、申请号和确认同族可合并；标题/日期/申请人相似只保留“疑似同族”提示；
6. 每一种排除原因都计数并持久化。

### 5.3 全文和同族

复用 `FetchedDocument` 的摘要、权利要求和说明书。Landscape 自己维护扩展快照：

- `current_assignee` 与来源；
- `original_assignee`；
- `family_id`；
- 确认同族成员列表；
- 每个成员的公开号、申请号、法域和日期；
- `family_data_status = COMPLETE | PARTIAL | UNAVAILABLE`。

来源无法证明时必须显示 `UNAVAILABLE/PARTIAL`，不得将 `possible_family_keys` 当作确认同族。

### 5.4 直接证据包（非 RAG）

每件专利的模型输入由确定性 Builder 直接从当前 `FetchedDocument` 构造：

- 摘要；
- 全部独立权利要求或受字符预算保护的权利要求段；
- 背景技术段；
- 命中已验证方向术语的说明书段；
- 段落类型、标签、offset 和 SHA-256。

不查询 `patent_chunks`，不生成 Embedding，不调用 Retriever，不写 Context Manifest。模型返回的证据别名必须绑定当前文献证据包。

### 5.5 逐件输出

`LandscapePatentAnalysis` 至少包含：

- `prior_art`；
- `prior_art_problems[]`；
- `core_invention_points[]`；
- `technical_problems_solved[]`；
- `beneficial_effects[]`；
- `technical_keywords[]`；
- `evidence_refs[]`；
- `limitations[]`。

申请号、标题、日期、权利人、同族和法域属于 Provider 事实，模型没有修改权。

### 5.6 聚类

MVP 不使用向量。Clusterer 只接收公开号、标题、摘要、核心发明点和技术关键词，生成 2～8 个簇并为每件成功分析文献指定一个主簇。程序必须校验：

- 成员只来自成功精读集合；
- 每件文献恰好进入一个主簇；
- 不重复、不遗漏、不产生新公开号；
- 只有一件文献时生成一个稳定的单文献簇；
- 聚类失败时保留逐件分析并以限制状态完成，不伪造聚类。

## 6. 数据和文件

独立 SQLite：`data/aifpatent/landscape.db`。

独立 Checkpoint：`data/langgraph/landscape-checkpoints.db`。

独立报告：`workspace/landscape-runs/{run_id}/`。

主要表：

- `landscape_runs`；
- `landscape_steps`；
- `landscape_queries`；
- `landscape_hits`；
- `landscape_run_documents`；
- `landscape_evidence`；
- `landscape_patent_analyses`；
- `landscape_clusters`；
- `landscape_cluster_members`；
- `landscape_reports`。

Run 输入、模式、窗口、友商别名、预算、模型名和 Prompt/Workflow 版本不可变；API Key 不进入任何列或文件。

报告文件：

- `input.json`；
- `report.json`；
- `report.md`；
- `patents.csv`；
- `manifest.json`。

Manifest 最后写入并记录所有文件 SHA-256；存在 Manifest 才代表报告完成。

## 7. API

```text
POST   /api/landscape/runs
GET    /api/landscape/runs
GET    /api/landscape/runs/{run_id}
GET    /api/landscape/runs/{run_id}/events
POST   /api/landscape/runs/{run_id}/cancel
POST   /api/landscape/runs/{run_id}/rerun
GET    /api/landscape/runs/{run_id}/report
GET    /api/landscape/runs/{run_id}/report.md
GET    /api/landscape/runs/{run_id}/patents.csv
DELETE /api/landscape/runs/{run_id}
```

创建 Run 请求携带当前页面临时 `base_url/model/api_key`。TaskManager 只在进程内持有 `RuntimeModelConfig`，终态、取消、异常和 shutdown 都清除。服务重启时将遗留 `QUEUED/RUNNING` 标记为 `RUNTIME_API_KEY_REQUIRED_AFTER_RESTART`，用户通过 rerun 创建新的不可变 Run。

## 8. 页面

`/landscape` 使用独立 HTML/JS/CSS，顶部导航可返回 `/`。页面三栏：

1. 历史分析 Run；
2. 输入、预算和实时步骤；
3. 概览、趋势、法域、聚类、逐件精读和限制。

第一版图表使用原生 HTML/CSS/SVG，不引入 npm、CDN 或新的镜像构建链。所有外部文本使用 `textContent`，不得把专利标题、模型内容或来源 HTML 注入 `innerHTML`。

## 9. 状态和失败语义

Run：`QUEUED | RUNNING | COMPLETED | COMPLETED_WITH_LIMITATIONS | FAILED | CANCELLED`。

文献：`CANDIDATE | EXCLUDED | FETCHED | ANALYZED | FAILED`。

以下情形允许限制完成：

- 部分 Provider 降级；
- 部分全文抓取失败；
- 当前权利人或同族数据缺失；
- 候选或精读预算截断；
- 个别专利模型分析失败；
- 聚类失败但至少一件精读成功。

以下情形必须失败：

- 输入范围非法；
- 所有 Provider 均不可用且无合格候选；
- 严格过滤后没有任何可识别新公开专利；
- 没有任何专利成功精读；
- 报告 Manifest 校验失败；
- 模型返回越界公开号或伪造证据。

## 10. 安全和质量门

- 报告统计只由程序从持久化事实计算，模型不能写数量、日期或国家分布；
- 公开日缺失文献不进入主统计；
- 当前权利人不可靠时显示未知，不回退伪装为当前权利人；
- 同族来源不明时不合并、不计入确认同族法域；
- 模型输入和输出日志只记录字符数、模型名、耗时和状态；
- 取消只影响当前 Landscape Run；
- 删除 Run 只删除其 Landscape 记录和报告目录，不删除共享 IDEA、Corpus 或 Provider Cache；
- 现有 IDEA 全量测试必须继续通过。

## 11. MVP 不包含

- 定时监控和自动周报；
- 实际执行 `NEW_RESEARCH` 的 IDEA Child Run；
- Embedding、pgvector、RRF 或 reranker；
- 法律状态、权利要求有效性和侵权判断；
- 保证完整的全球同族数据；
- 用户、组织、权限和多租户；
- 分布式多 Worker 自动恢复。

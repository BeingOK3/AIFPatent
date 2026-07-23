# 专利态势分析 MVP 架构

状态：`APPROVED_FOR_IMPLEMENTATION`

日期：2026-07-22

## 1. 目标

为内部研究人员提供独立页面，分析：

1. 一个具体技术方向在指定公开日期窗口内的新公开专利；或
2. 一个或多个重点友商在指定公开日期窗口内的新公开专利。

输出同时包含确定性统计和逐件模型精读：

- 唯一合格专利的公司数量分布；
- 公开号法域分布和已确认同族/全族状态；
- 技术聚类；
- 每件专利的申请号、名称、申请日、当前权利人、同族布局；
- 现有技术、现有技术问题、核心技术发明点、解决的技术问题和有益效果；
- 检索覆盖、排除原因、数据缺失和失败限制。

## 2. 输入语义

### 2.1 模式

页面不要求用户预选模式，而是根据两类输入确定性派生，并在提交前展示：

- `TECHNOLOGY`：只填写技术方向，检索该方向内所有申请人；
- `COMPETITOR`：只填写重点友商，按友商及系统生成别名严格过滤；
- `TECHNOLOGY_COMPETITOR`：同时填写技术方向和重点友商，检索技术方向与友商的交集。

后端重新计算派生模式；客户端传入模式与实际输入不一致时拒绝请求。派生模式进入不可变 Run 输入、数据库、API 和报告，模型不能改变。

### 2.2 时间

“新公开”以公开日为硬条件，窗口两端均包含：

```text
publication_start <= publication_date <= publication_end
```

预设支持 1 个月、1 个季度、6 个月、12 个月和自定义日期，MVP 最长 12 个月。申请日继续作为逐件与聚类成员元数据展示，但不再生成申请日趋势图，也不得把申请日当作检索窗口。

### 2.3 友商别名

页面按行输入友商主名称，不要求用户维护别名。`PLAN_SEARCH` 使用模型为每个主名称生成用于专利申请人检索的中英文名称、常用公司全称和历史名称，并遵守：

- 输出必须逐一对应用户输入的主名称，不能增加新的友商主体；
- 每个友商最多 12 个别名，程序做 NFKC、大小写、空白和重复校验；
- 原始主名称始终进入严格匹配集合；
- 模型失败时降级为只使用主名称，并在报告中标注；
- 实际使用的主名称、模型生成别名和来源 `MODEL_INFERRED` 必须进入查询阶段结果、调试接口和最终报告。

友商别名会影响严格申请人过滤，因此报告不得把它们描述为已经完成工商主体核验的法律名称。

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

- 技术方向由结构化模型扩展为原始词、中文同义技术词和英文专利检索词；模型失败时保留原词并标注降级；
- 技术方向模式至少包含原始语言查询和英文查询；原始输入不能被翻译结果替换；
- 友商模式必须为每个用户输入友商生成至少一条独立查询，任何友商不得因为总查询上限或前序友商别名过多而被跳过；
- 联合模式必须为每个友商分别生成“技术方向 × 该友商”的查询，并同时覆盖中文/原始技术词组和英文技术词组；
- 同一友商的主名称与合法同主体别名可合并为有界 OR 组，查询总量上限按友商数动态计算，最多 40 条；
- 可审计的方向术语；
- 每条 query 的语言和理由；
- 不包含 Provider Token、日期结论或搜索结果断言。

查询规划校验逐一检查所有友商覆盖，不能再使用“任意一个友商命中即通过”。友商别名、公开日窗口和预算始终由程序追加或作为 Provider 参数传入，模型不能改写。

### 5.2 搜索和过滤

复用现有 `SearchProvider`、`ProviderRunner`、`GooglePatentsProvider`、`ExaMcpProvider`、`SerpApiPatentProvider` 和 `merge_hits`，但在新业务服务中建立独立规则：

1. Provider 侧尽量加入公开日/申请人条件；
2. 返回后再次解析 ISO 公开日并做包含式硬过滤；
3. `COMPETITOR` 模式按用户确认别名匹配申请人字段；
4. 公开号无效、公开日缺失或超窗不得进入正式结果；
5. 公开号、申请号和确认同族可合并；标题/日期/申请人相似只保留“疑似同族”提示；
6. 每一种排除原因都计数并持久化。

公司数量分布使用严格过滤、跨查询去重后的全部唯一合格专利计算，不使用会重复的原始命中，也不使用已经受 `candidate_limit` 截断的候选子集。候选和精读的评分、公司覆盖及补位规则见 `selection-report-v2-design.md`。

Provider 执行采用每 Provider 有界并发：Exa 避免同一 Run 突发请求触发 429；Google Patents 首次网络超时后在当前 Run 快速熔断。Provider 专用日期提示分别构造，Google 查询语法不得原样传给通用 Web Search。

SerpAPI 使用 `google_patents` 引擎，并把公开日起止作为 `after=publication:YYYYMMDD`、`before=publication:YYYYMMDD` 的独立参数传入；检索结果直接映射公开号、申请日、公开日、申请人和同族法域状态。详情使用 `google_patents_details` 引擎，摘要和权利要求进入 `FetchedDocument`，不得把 API Key 写入 URL 日志、Run 配置、数据库或报告。

三路 Provider 的职责边界为：

- SerpAPI：结构化主召回和首选详情源，承担全部确定性查询；
- Exa MCP：自然语言/英文技术词的语义补召回，弥补 Google Patents 关键词排序遗漏；
- Google Patents 直连：零外部 API 计费的补充源，只在网络健康时使用，不作为可部署性的单点依赖。

第一阶段为保证行为可审计，所有已启用且具备运行凭证的 Provider 接收相同完整查询计划，结果统一去重；后续可在不改变查询计划与严格过滤语义的前提下增加 `COMPLETE | BALANCED | ECONOMY` 调度策略。默认建议 `BALANCED`：SerpAPI 执行全部查询，Exa 只执行技术语义查询，Google 仅在健康检查成功时执行。任何节流都必须在 Debug 中标记为 `POLICY_SKIPPED`，不能伪装成已检索。

公开日补全先按 `provider + publication_number` 去重，再受候选预算限制执行；同一专利跨多个查询命中只抓取一次，补全统计须进入调试信息。

### 5.3 全文和同族

复用 `FetchedDocument` 的摘要、权利要求和说明书。Landscape 自己维护扩展快照：

- `current_assignee` 与来源；
- `original_assignee`；
- `family_id`；
- 确认同族成员列表；
- 每个成员的公开号、申请号、法域和日期；
- `family_data_status = PARTIAL | UNAVAILABLE`；
- 结构化全族成员的法域、申请号、申请日、法律状态类别、法律状态文本和是否当前申请；
- 由结构化成员状态确定性归纳的 `ACTIVE | INACTIVE | MIXED | UNKNOWN`。

来源无法证明时必须显示 `UNAVAILABLE/PARTIAL`，不得将 `possible_family_keys` 当作确认同族，也不得宣称 Provider 返回的部分成员构成全球完整同族。

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

报告层按公开号把 Provider 事实连接到聚类成员，每个成员显示确认友商（可匹配时）、当前权利人和申请日；Clusterer 无权生成或修改这些字段。

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
GET    /api/landscape/runs/{run_id}/debug
DELETE /api/landscape/runs/{run_id}
```

创建 Run 请求只携带当前页面临时 `base_url/model/api_key`。SerpAPI 使用服务器本地 `config/provider-credentials.local.json`；该文件被 Git 和 Docker 构建上下文忽略，Docker 运行时通过只读 Secret 挂载，仓库只提交 `provider-credentials.example.json` 模板。Run 仅记录凭证来源 `local_json`，不保存值。模型 TaskManager 仍只在进程内持有模型配置，终态、取消、异常和 shutdown 都清除；服务重启时将遗留 `QUEUED/RUNNING` 标记为 `RUNTIME_API_KEY_REQUIRED_AFTER_RESTART`，用户通过 rerun 创建新的不可变 Run。

## 8. 页面

`/landscape` 使用独立 HTML/JS/CSS，顶部导航可返回 `/`。页面包含：

1. 历史分析 Run；
2. 输入、预算和实时步骤；
3. 概览、公司专利数量柱状图、法域、聚类、逐件精读和限制；
4. 运行调试：当前节点、尝试、查询、Provider 状态、命中/排除计数和错误。

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

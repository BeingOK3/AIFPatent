# 专利态势重构开发规格

状态：`approved-technical-baseline`

版本：`1.1`

建立日期：`2026-08-03`

产品事实源：[`spec.md`](spec.md)

分类事实源：[`classify.md`](classify.md)

## 0. 文档职责

本文是新版本专利态势的唯一技术开发基线，定义目标架构、领域模型、持久化、
任务调度、模型调用、检索分页、专利族、分类、趋势、API、页面、迁移、测试和
实施顺序。

本文不得增加、删除或改写 `spec.md` 的产品能力。发生冲突时，以 `spec.md` 为
准并停止开发；先升级产品规格，再同步升级本文。

旧 Landscape 源码只用于迁移审计，不能反向决定新架构。旧 API、旧报告和旧
Run 不属于新分支的兼容目标。

本项目采用 clean-slate 切换：不做旧配置转换、旧 API Adapter、旧报告重建或
新链失败后回退旧链。为不破坏开发期可运行性，旧模块只能保留到对应新能力
通过测试并接管入口；随即在同一工作包内删除，不累积长期兼容债务。

## 1. 最高优先级：稳定性

稳定性优先于功能数量、极限并发和表面上的低延迟。以下是硬约束：

1. PostgreSQL 是运行状态和业务结果的唯一事实源；
2. 模型、Provider 和进程内内存都不是事实源；
3. 所有外部调用必须对应持久化任务、稳定任务键和输入 Hash；
4. 所有可重试任务采用至少一次执行语义，写入必须幂等；
5. 每件专利、每个批次和每个分片成功后立即 checkpoint；
6. 重试作用于最小失败对象，不重做整个 Run；
7. 取消、进程退出和服务重启后，不丢失已成功结果；
8. 模型叙述失败不得使确定性分类和统计报告丢失；
9. 集合不一致、身份冲突和持久化损坏必须 fail closed；
10. 任何截断、降级和未完成对象必须进入用户可见限制；
11. 不在单个 Prompt、Graph State 或进程字典中保存全部 500/1000 件文本；
12. 真实 500/1000 件、重启恢复和故障注入未通过前，不得宣布开发完成。

### 1.1 Run 状态

| 状态 | 含义 |
|---|---|
| `COMPLETED` | 所有硬不变量通过，报告完整，无影响结论的缺口 |
| `COMPLETED_WITH_LIMITATIONS` | 核心分类和统计有效，但存在明确的 Provider、摘要、模型或趋势限制 |
| `FAILED` | 身份集合、数据库完整性、分类对账或报告完整性无法保证 |
| `CANCELLED` | 用户取消；已完成结果和审计保留 |
| `WAITING_FOR_CREDENTIALS` | 服务重启或凭证租约失效；已完成结果保留，等待用户重新挂载模型凭证 |

单件模型失败、摘要缺失、某个趋势叙述失败不能单独触发全局 `FAILED`。
`WAITING_FOR_CREDENTIALS` 是可恢复业务状态，不是失败终态；Run 恢复后仍必须进入
`COMPLETED`、`COMPLETED_WITH_LIMITATIONS`、`FAILED` 或 `CANCELLED`。

## 2. 当前实现审计与迁移边界

### 2.1 可以复用

- FastAPI 路由装配和 BYOK 基础校验；
- PostgreSQL 连接、迁移执行器和 Run 基础记录；
- Run 历史、SSE、取消和报告下载机制；
- SerpAPI 响应解析、缓存、错误脱敏和凭证文件；
- keyed step、任务尝试次数和幂等写入思想；
- 允许域名外链和报告 Artifact Store 的基础能力。

### 2.2 必须替换

- `candidate_limit <= 200`、`analysis_limit <= 50` 的旧预算模型；
- 创建 Run 后立即自动运行的一阶段 API；
- 公司别名和技术词未经用户确认直接生效的规划流程；
- SerpAPI 只读取一页结果的 Provider 接口；
- 把多个公司名称当成不可靠 Provider OR 条件的检索方式；
- 读取 claims、description 和可选 Deep Read 的证据路径；
- 按公司分别让模型临时生成技术类别的分类服务；
- 向模型发送全量公司画像和专利上下文的跨公司趋势服务；
- 当前固定主链和旧 Report Schema；
- 前端单阶段创建表单和精读交互。

### 2.3 新链完成后删除

- `backend/landscape/analysis.py` 中全文/权利要求分析；
- `backend/landscape/clustering.py` 的旧全局模型聚类；
- `backend/landscape/deep_selection.py`；
- 旧 Company Classification/Profile/Trend 实现中无新调用的部分；
- `/deep-analyze` API 和前端精读按钮；
- 旧报告兼容重建分支；
- 只服务旧工作流的 Schema、表和测试。

删除必须发生在新链、迁移验证和 import/call-site 扫描通过之后，不允许先删后补。
旧配置 Schema、环境变量、前端表单字段和测试 Fixture 与对应旧代码同步删除；
禁止为了让旧测试继续通过而保留已废弃的产品行为。

## 3. 端到端目标架构

```text
用户输入
  → PREPARE_SCOPE_DRAFT
      ├─ LOAD_COMPANY_MEMORY
      ├─ EXPAND_COMPANY_NAMES
      └─ EXPAND_TECHNOLOGY_TERMS
  → AWAIT_USER_SCOPE_CONFIRMATION
  → CONFIRM_AND_FREEZE_SCOPE
  → SEARCH_PUBLICATIONS              分页、每页 checkpoint
  → AWAIT_SCALE_CONFIRMATION          结果 > 默认规模时
  → FILTER_DEDUP_AND_FREEZE
  → RESOLVE_CONSERVATIVE_FAMILIES
  → FETCH_ABSTRACTS                   单件任务
  → EXTRACT_DIRECTIONS                动态批次
  → MATCH_TAXONOMY                    动态批次 + 有界复核
  → DISCOVER_OTHERS                   仅 Others
  → COMPUTE_METRICS                   程序
  → BUILD_TREND_CANDIDATES            程序
  → NARRATE_TRENDS                    有界模型任务
  → SELECT_REPRESENTATIVES            程序
  → VERIFY_RUN
  → BUILD_REPORT
```

人机确认和大规模确认是持久化业务状态，不依赖长时间占用 HTTP 请求或内存中的
LangGraph interrupt。

## 4. 编排与任务执行

### 4.1 LangGraph 边界

LangGraph 只负责阶段级协调、条件路由和完成门，不承载数百或数千件专利文本。
Graph State 只允许包含：

- `run_id`；
- 当前阶段；
- 当前分片 ID；
- 已完成/失败计数；
- 审计决策；
- 限制代码。

逐件和逐批任务由 PostgreSQL 任务队列执行，不能为每件专利构造永久 Graph State。

### 4.2 PostgreSQL 任务队列

每个任务至少包含：

```text
task_id
run_id
shard_id
task_type
task_key
input_hash
status
attempt
credential_profile_id
lease_owner
lease_expires_at
next_attempt_at
started_at
completed_at
error_code
error_message
output_ref
```

唯一约束：

```text
UNIQUE(run_id, task_type, task_key, input_hash)
```

Worker 使用 `FOR UPDATE SKIP LOCKED` 领取任务。任务租约超时后可以重新领取；
最终写入使用唯一约束和 CAS，防止两个 Worker 重复提交。

### 4.3 任务状态

```text
PENDING → LEASED → SUCCEEDED
                ├→ RETRY_WAIT → PENDING
                ├→ WAITING_CREDENTIALS → PENDING
                ├→ LIMITED
                └→ FAILED
```

可恢复任务不得只保存在 `asyncio.Task`、字典或临时文件中。

## 5. 范围草稿与用户确认

### 5.1 两阶段资源

正式 Run 之前建立 `ScopeDraft`：

```text
DRAFT
→ EXPANDING
→ AWAITING_CONFIRMATION
→ CONFIRMED
```

草稿可以在页面刷新后继续审查。API Key 不保存；需要重新调用模型时由用户再次
提供瞬时凭证。

### 5.2 公司档案

核心表：

```text
landscape_company_profiles
landscape_company_profile_versions
landscape_company_names
```

`CompanyNameEntry`：

```json
{
  "name_id": "CNM-...",
  "display_name": "Huawei Technologies Co., Ltd.",
  "normalized_name": "huawei technologies co ltd",
  "language": "en",
  "relation_type": "LEGAL_NAME",
  "source": "USER_CONFIRMED",
  "status": "ACTIVE"
}
```

`relation_type`：

```text
LEGAL_NAME | TRANSLATION | ALIAS | FORMER_NAME | SUBSIDIARY | GROUP_MEMBER
```

`status`：

```text
PROPOSED | ACTIVE | REJECTED | RETIRED
```

别名和集团成员必须分开。模型只能创建 `PROPOSED`；只有用户确认可以创建
`ACTIVE`。用户拒绝项保留为 `REJECTED`，避免以后重复建议。

页面必须提供：

- “仅本次排除”；
- “从长期档案停用”。

每次确认生成不可变公司档案版本。旧 Run 始终引用当时版本。

### 5.3 技术词草稿

每个技术词包含：

```text
term_id
text
language
relation_to_original
source
status
```

模型生成中英文候选，用户确认后形成不可变技术词快照。模型不得在正式搜索阶段
再增加词语。

### 5.4 API

```text
POST   /api/landscape/scope-drafts
GET    /api/landscape/scope-drafts/{draft_id}
PATCH  /api/landscape/scope-drafts/{draft_id}
POST   /api/landscape/scope-drafts/{draft_id}/expand
POST   /api/landscape/scope-drafts/{draft_id}/confirm
POST   /api/landscape/runs
POST   /api/landscape/runs/{run_id}/scale-decision
POST   /api/landscape/runs/{run_id}/credentials
```

`POST /runs` 只接受已确认的 `scope_revision_id`，不再接收未经审查的公司别名和
技术词。

## 6. 检索、分页与范围冻结

### 6.1 Provider 接口

旧接口：

```text
search(query) -> hits
```

替换为：

```text
search_page(query, cursor) -> SearchPage
```

`SearchPage` 至少包含：

```text
hits
next_cursor
page_number
reported_total_results
reported_total_pages
provider_request_id
stop_reason
```

每页写入成功后才请求下一页。重启后从最后一个成功 cursor 恢复。

### 6.2 SerpAPI

SerpAPI `page` 用于分页，`num` 最大 100。实现必须消费 `serpapi_pagination.next`
或等价页码，直到：

- 没有下一页；
- Provider 明确硬上限；
- 下一页不产生新公开号；
- 用户批准的范围预算已达到；
- Provider 配额或错误触发受限停止。

必须记录真实停止原因，禁止命中上限后记录 `truncated_count=0`。

### 6.3 查询计划

逻辑规则：

```text
COMPANY_ONLY:
  confirmed assignee name

TECHNOLOGY_ONLY:
  confirmed Chinese/English term group

COMPANY_AND_TECHNOLOGY:
  confirmed assignee name AND confirmed technology group
```

每家公司必须有独立检索机会。Provider 对多个申请人 OR 的语义在真实验证前不
作为正确性依赖；默认一个确认申请人名称对应一个可审计查询。查询合并只能作为
验证后的优化，不能减少公司覆盖。

技术词过长时按确定性规则拆成多个词组查询，再跨查询去重。查询 ID、词组和公司
范围必须可回放。

为避免模型倾向填满候选集合而造成审查负担和查询笛卡尔积，单次公司扩展最多
保留 12 个新增名称候选（其中子公司/集团成员优先保留最多 4 个高置信候选），
单次技术方向扩展最多保留 16 个新增双语词。达到上限必须在 Scope Draft 中显示
限制；用户仍可在确认前手工增删。该候选预算不等于最终专利数量上限。

### 6.4 Scale Gate

第一批页面获得 Provider 估算总量后：

- 预计不超过 500：继续；
- 预计 501～1000：显示规模和成本，允许单次确认；
- 预计超过 1000：显示规模、预计分片数、成本和时间，等待用户确认；
- 用户可以返回修改公司、技术词或时间窗。

确认后冻结成员规则和预算。禁止先静默截断再生成完整性声明。

## 7. 专利身份与专利族

### 7.1 双层身份

系统同时维护：

```text
PublicationRecord  公开号级公开文本
AnalysisUnit       分类和趋势使用的发明单元
```

所有检索结果先保存为 Publication，不在搜索结果入口直接不可逆地折叠 Family。

### 7.2 当前 SerpAPI 策略

SerpAPI Google Patents 默认可以按 Family 分组，`dups=language` 返回 Publication。
新版本首版仍使用 Publication 模式保证公司、日期和成员范围可审计；Family 分组
作为检索后的保守去重层，而不是唯一检索来源。

### 7.3 保守合并规则

按以下顺序建立 `AnalysisUnit`：

1. 公开号完全相同：直接去重；
2. 同一申请号的不同公开阶段：合并为同一申请单元；
3. 优先权集合完全相同且数据源可确认属于简单专利族：合并；
4. 仅共享部分优先权、分案、继续申请或扩展族：不合并，只记录关系；
5. Family/优先权数据不完整或冲突：按公开号保守分开。

Provider `family_id` 只是带来源的线索，不能单独成为合并权威。

### 7.4 Family 数据获取

SerpAPI Details 可提供 `family_id`、`worldwide_applications`、parent/child/priority
applications，但不会一次返回全部成员摘要。系统只获取成员书目信息，不获取
全部全文。

可以增加 EPO OPS Family Service 作为可选家族成员补充，但它返回 INPADOC 扩展
族，不能直接作为简单族去重规则。EPO 不可用时核心流程仍可运行。

### 7.5 代表摘要

每个可靠合并单元只选择一个代表摘要执行分类，顺序为：

1. 摘要存在且信息完整；
2. 中英文可直接处理；
3. 本次公开时间窗内；
4. 当前公司范围可确认；
5. WO、最早公开或稳定优先级；
6. 公开号作为确定性 tie-breaker。

若家族成员摘要显示明显不同技术方向，创建 Family Conflict，停止自动合并并
恢复为多个分析单元。

报告同时输出：

- `publication_count`；
- `analysis_unit_count`；
- 每个分析单元的全部成员和链接；
- Family 数据覆盖和保守拆分数量。

## 8. 摘要获取与证据

### 8.1 轻量接口

Provider 增加：

```text
fetch_bibliographic_abstract(publication)
```

只消费：

- 标题；
- 摘要和原始语言摘要；
- 公开号、申请号；
- 申请机构；
- 优先权、申请日、公开日；
- CPC/IPC；
- Family/关系书目信息；
- 外部链接字段。

不得请求 `description_link`，不得把 claims 或 description 写入分类证据。

### 8.2 Evidence Sentence

摘要规范化后按句切分：

```text
EV-{analysis_unit_id}-T       标题
EV-{analysis_unit_id}-A01     摘要句 1
EV-{analysis_unit_id}-A02     摘要句 2
...
```

模型只引用这些 Evidence ID。程序检查 Evidence 属于当前分析单元，且引用句真实
存在。原摘要、规范化摘要、内容 Hash 和 Provider 来源必须持久化。

### 8.3 摘要终态

```text
AVAILABLE | MISSING | INVALID | PROVIDER_FAILED
```

摘要不可用时进入 `UNRESOLVED`，但 Run 可以受限完成。

## 9. 分类标准编译

### 9.1 `classify.md` 是人工源

运行时不能每次临时解析并猜测分类。构建阶段执行 Taxonomy Compiler：

1. 解析 Markdown 表格；
2. 规范 Unicode、空白和大小写；
3. 校验一级/二级/三级层级；
4. 校验重复完整路径；
5. 允许三级为空，二级成为叶子；
6. 为完整路径生成稳定 `category_id`；
7. 生成 canonical JSON；
8. 计算 `taxonomy_hash`；
9. 写入 PostgreSQL 并保存不可变 Artifact。

建议 ID：

```text
CAT-{sha256(normalized_full_path)[0:16]}
```

同名二级分类在不同一级分类下具有不同 ID。分类重命名会产生新版本；旧 Run 继续
引用旧 ID 和快照。

### 9.2 启动门禁

分类表存在空一级/二级、重复路径、非法列或 Hash 与数据库记录不一致时，服务
readiness 失败，不能带着模糊分类上线。

## 10. 模型能力与动态批次

### 10.1 不信任硬编码上下文

模型能力来自服务器可审计配置：

```text
provider
base_url
model
max_context_tokens
max_output_tokens
rpm
tpm
max_concurrency
capability_version
```

不能把套餐总额度、上下文缓存或累计 Token 当作单请求上下文。当前 DeepSeek 官方
V4 Pro/Flash 公开上下文为 1M；Ark 若提供不同能力，必须通过其模型配置和受控
探针确认，不能直接写死 100M。

### 10.2 模型角色

运行配置支持：

```text
bulk_model       默认 Flash，处理方向抽取和明确分类
reasoning_model  默认 Pro，处理冲突、Others 复核和趋势叙述
```

只有一个模型时两者可以相同，不要求两套 API Key。

### 10.3 TokenEstimator

优先使用模型对应 tokenizer；不可用时使用保守估算并增加安全余量。每件任务估算：

```text
固定 Prompt
+ 标题/摘要 Evidence
+ 分类候选
+ 预期结构化输出
```

### 10.4 动态装箱

批次不是固定专利数，而由以下硬限制共同决定：

```text
max_batch_input_tokens
max_batch_output_tokens
max_batch_items
max_taxonomy_candidates
```

安全输入预算：

```text
usable_input =
  min(configured_context, verified_context)
  * safety_ratio
  - fixed_prompt
  - reserved_output
```

首版 `safety_ratio` 为 0.60～0.70。初始建议：

| 模型角色 | 目标件数 | 硬上限 |
|---|---:|---:|
| Flash/Bulk | 24～40 | 50 |
| Pro/Reasoning | 12～24 | 32 |

实际批次由确定性装箱产生，相同成员、模型能力版本和输入 Hash 必须得到相同批次。

### 10.5 批次失败隔离

1. 严格验证每个返回记录；
2. 合法记录立即持久化；
3. 缺失或非法记录重新排队；
4. 整批失败时原批重试一次；
5. 再失败则一分为二；
6. 最终隔离到单件；
7. 单件耗尽重试后进入 `UNRESOLVED` 或任务限制，不失败整个 Run。

运行中只允许失败拆小，不动态扩大已经冻结的 Batch Profile。遥测用于生成下一
Run 的新 Profile，避免同一 Run 因实时波动产生不可重放批次。

### 10.6 Prompt Cache

固定系统 Prompt、输出 Schema 和 Taxonomy 公共前缀保持字节稳定，争取 Provider
缓存命中。缓存只降低成本和延迟，不能成为正确性依赖。

## 11. 方向抽取 Agent

### 11.1 输入

每件专利只包含：

- `analysis_unit_id`；
- 代表公开号；
- 标题 Evidence；
- 摘要句 Evidence；
- 可选 IPC/CPC；
- 当前 Taxonomy 的一级分类 ID 与名称。

公司名称、日期、排名不能影响技术语义分类。

### 11.2 输出

```json
{
  "analysis_unit_id": "AU-...",
  "evidence_sufficient": true,
  "technical_problem": "...",
  "solution_mechanism": "...",
  "technical_object": "...",
  "application_scenarios": ["..."],
  "direction_summary": "...",
  "keywords": ["..."],
  "candidate_level1_ids": ["CAT-..."],
  "confidence": 0.87,
  "evidence_ids": ["EV-..."]
}
```

程序校验：

- Analysis Unit ID 必须存在且属于当前任务；
- Evidence ID 必须属于当前摘要；
- 一级候选必须来自冻结 Taxonomy；
- 不允许空泛的“提高效率”成为唯一技术机制；
- 关键词、列表长度和文本长度有界。

摘要不足时直接输出 `UNRESOLVED` 原因，不调用全文工具。

## 12. Taxonomy 匹配 Agent

### 12.1 候选召回

不把完整分类表重复发送给每批模型。候选集合取并集：

- 方向 Agent 的一级候选下全部叶子；
- 中文字符 n-gram/TF-IDF Top-K；
- 英文词、缩写和分类名称 Top-K；
- IPC/CPC 映射的弱候选；
- 可选 Embedding Top-K。

第一版外部 Embedding 不是强依赖。Embedding 关闭时，词法和层级 Agent 路径必须
完整可用，并明确记录 `LEXICAL_HIERARCHICAL`。

### 12.2 模型动作

```text
EXACT_CATEGORY
NONE_OF_CANDIDATES
NEEDS_ALTERNATIVE_PARENT
UNRESOLVED
```

`EXACT_CATEGORY` 必须返回一个主分类和零到多个辅助标签。

### 12.3 有界复核

`NONE_OF_CANDIDATES` 或 `NEEDS_ALTERNATIVE_PARENT` 最多执行一轮父类复核：

1. 向模型提供完整一级分类名称，不提供全部叶子；
2. 模型选择最多三个替代一级分类或明确没有适用父类；
3. 程序加载对应叶子执行最终匹配；
4. 摘要充分且仍无适用路径时才进入 `OTHERS`；
5. 摘要不足或结果冲突时进入 `UNRESOLVED`。

禁止无限反思或无限扩大分类候选。

### 12.4 分类对账

```text
Frozen Analysis Units
= CLASSIFIED
+ OTHERS
+ UNRESOLVED
```

三个集合必须互斥、无重复且无遗漏。对账失败时不得生成成功报告。

## 13. Others 方向发现

只有 `OTHERS` 成员进入该阶段。

### 13.1 成员关系

方向记录使用：

- problem；
- mechanism；
- object；
- scenarios；
- keywords；
- direction summary。

首版使用本地字符 n-gram/词法向量和约束层次聚类。若部署级 Embedding 可用，
可以加入语义相似度，但必须记录算法和模型版本。

禁止普通 Connected Components，避免弱边链式误合并。允许单件方向和 Noise。

### 13.2 模型职责

程序确定 Cluster ID 和成员；模型只根据中心、边界和代表记录生成：

- 新方向名称；
- 技术问题；
- 共同机制；
- 方向边界；
- 关键词。

模型不能移动、增加或删除成员。Others 新方向只属于当前 Run，不自动写回
`classify.md`。

## 14. 统计与趋势

### 14.1 程序数据立方体

```text
category_or_other_direction
× organization
× time_bucket
```

程序计算：

- Analysis Unit 数量；
- Publication 数量；
- 分类占比；
- 公司/学校分布；
- 时间桶数量和占比；
- 新出现、持续活跃、增强、减弱候选；
- 数据缺口和置信门槛。

### 14.2 时间桶

首版 `AUTO`：

| 时间窗 | 时间桶 |
|---|---|
| ≤ 6 个月 | 月 |
| > 6 且 ≤ 24 个月 | 季度 |
| > 24 个月 | 年 |

强趋势至少需要三个有效时间桶和足够 Analysis Unit；否则只输出当前布局或前后期
观察。具体样本阈值必须通过 Fixture 校准，并保存为版本化策略。

### 14.3 TrendCandidate

程序先生成有限候选，模型不能浏览全部摘要。每个候选包含：

- category/direction ID；
- organization IDs；
- 时间桶指标；
- 变化类型候选；
- 代表 Analysis Unit IDs；
- 合法 Evidence IDs；
- 允许的结论强度。

模型只能解释候选，不能修改数量、成员、公司归属或趋势方向门槛。

### 14.4 模式视图

- `TECHNOLOGY_ONLY`：技术分类 → 公司/学校；
- `COMPANY_ONLY`：公司 → 技术分类；
- `COMPANY_AND_TECHNOLOGY`：技术分类 → 用户确认公司。

单公司不生成虚假跨公司比较。

## 15. 代表专利和外链

程序按以下因素确定代表 Analysis Unit：

1. 分类置信度；
2. 摘要证据完整性；
3. 与方向中心的接近程度；
4. 时间代表性；
5. 公司/学校多样性；
6. Family 去重；
7. Analysis Unit ID 确定性 tie-breaker。

模型只能解释入选理由，不能增加公开号。

链接模板：

```text
https://patents.google.com/patent/{publication_number}
```

URL 由程序生成，域名必须在 Allowlist，页面使用 `target="_blank"` 和
`rel="noopener noreferrer"`。

## 16. 并发、限流与熔断

### 16.1 全局调度器

所有 Landscape 模型任务共享一个调度器，同时约束：

- 全局在途请求；
- 每种 Agent 的在途请求；
- RPM；
- TPM；
- 估算在途输入 Token；
- Provider `Retry-After`；
- 模型角色公平队列。

初始全局并发建议 6～8，验证后上限可配置到 16 或 Provider 允许值。不能因为远端
并发额度很高就一次创建数千个本地任务。

### 16.2 Provider 调度器

检索分页和摘要获取分别限流。每个 Provider 配置：

```text
max_concurrency
rpm
timeout
max_attempts
circuit_breaker_threshold
cooldown
```

429 遵守 `Retry-After`；连续认证错误立即熔断；5xx、连接错误使用指数退避加
jitter。重试不得重新创建已成功数据库记录。

### 16.3 内存边界

- Worker 从数据库领取有界任务；
- 每个 Worker 只持有当前批次；
- 完成后释放摘要、Prompt 和响应；
- 不在 Execution Service 中缓存整 Run documents/fingerprints；
- 报告使用流式或分页读取聚合。

### 16.4 凭证租约与重启恢复

为同时满足“API Key 不落盘”和“已成功结果可恢复”，模型凭证使用显式
`CredentialLease`：

- 数据库只保存非秘密的 `credential_profile_id`、Provider、Base URL、Model 和
  能力配置 Hash；
- API Key 仅保存在当前进程的受控内存租约中，终止、超时或取消时清除；
- Worker 领取需模型的任务前先检查租约，没有凭证时把任务转为
  `WAITING_CREDENTIALS`，不消耗重试次数；
- Run 在存在未完成的模型任务且无租约时显示 `WAITING_FOR_CREDENTIALS`；
- 用户通过 `POST /api/landscape/runs/{run_id}/credentials` 重新挂载 Key；系统验证
  Base URL、Model 和能力 Hash 与冻结配置一致后，把等待任务放回 `PENDING`；
- 重新挂载凭证不重做 `SUCCEEDED` 任务，也不改变已冻结的 Scope、Taxonomy
  和模型配置；
- 若用户要更换模型或能力配置，必须显式创建新 Run，防止同一 Run 中结果
  语义漂移。

因此，“服务重启恢复”的准确含义是：已成功的 Provider/模型结果和任务
进度不丢失；仅待调用的模型任务需用户重新提供凭证后续跑。

## 17. 分片

默认规模 500，单次最大规模 1000。超过 1000 时：

1. 对冻结 Analysis Unit 按稳定顺序排序；
2. 每 1000 件形成一个 `RunShard`；
3. Shard 独立调度摘要、方向和分类任务；
4. Taxonomy Version、Scope Version 和策略版本完全相同；
5. Shard 完成后进入全局 Others、统计和趋势阶段；
6. 合并前执行无重复/无遗漏审计。

分片只改变执行调度，不能产生分片内独立 Taxonomy 或独立公司分类。

## 18. 持久化模型

新迁移从 `080_` 开始。核心逻辑表：

```text
landscape_company_profiles
landscape_company_profile_versions
landscape_company_names

landscape_scope_drafts
landscape_scope_revisions
landscape_scope_companies
landscape_scope_terms

landscape_taxonomy_versions
landscape_taxonomy_categories

landscape_runs
landscape_run_shards
landscape_tasks

landscape_search_queries
landscape_search_pages
landscape_publications
landscape_publication_sources

landscape_family_groups
landscape_family_members
landscape_analysis_units

landscape_abstract_evidence
landscape_direction_records
landscape_classification_results

landscape_other_clusters
landscape_other_cluster_members

landscape_metrics
landscape_trend_candidates
landscape_trend_narratives
landscape_representative_patents
landscape_reports
landscape_run_audits
```

重要业务字段必须关系化；模型原始响应、调试数据和扩展字段可以使用有 Schema
Version 的 JSONB。不得把全部业务状态塞入一个 JSON 列。

### 18.1 幂等身份

```text
scope_revision_hash
taxonomy_hash
query_hash
publication_identity_hash
abstract_content_hash
direction_input_hash
classification_input_hash
batch_profile_hash
trend_candidate_hash
```

所有模型和 Provider 结果必须可由输入 Hash 判断是否可安全复用。

### 18.2 数据不变量

- 每个 Run 只引用一个 confirmed Scope Revision；
- 每个 Run 只引用一个 Taxonomy Version；
- 每个 Publication 在 Run 内唯一；
- 每个 Publication 最多属于一个 Analysis Unit；
- 每个 Analysis Unit 恰好一个分类终态；
- 每个 Others 成员最多属于一个 Others Cluster；
- 每个代表专利属于对应方向；
- Shard 成员并集等于 Run 冻结全集；
- 报告计数可从底层成员复算。

### 18.3 新旧数据切换

- 现有 `landscape_runs`、`landscape_reports` 等同名表先做 Schema 对照；迁移只执行
  显式 `ALTER` 或创建新表，不假设空库；
- 新 Run 必须持久化 `workflow_version = landscape-v4`，旧 Run 保持只读，不迁移为
  新报告，也不在 v4 页面/API 中继续提供兼容访问；
- 不做新旧双写，不在新链路中保留“尝试新版、失败退回旧版”的隐式分支；
- 每个迁移在带真实旧 Schema 的数据库副本上做前向演练，验证旧数可读、
  新 Run 可写、重复执行无副作用；
- v4 真实验收通过之前不删表；通过后删除已无 import 和 call-site 的旧
  Landscape 表/列，不为旧 Run 长期保留运行时依赖。

本分支不承诺保留旧 Run 和旧报告；开发期先不损坏旧记录只是为了降低切换
风险，不是产品兼容承诺。

## 19. API 与页面

### 19.1 页面流程

页面拆成：

1. 输入公司、技术方向和时间窗；
2. 查看历史公司档案和模型建议；
3. 增、改、删公司名称和技术词；
4. 确认范围；
5. 查看 Provider 估算规模；
6. 必要时确认 501～1000 或分片；
7. 查看检索、摘要、分类、Others、趋势进度；
8. 查看报告、限制和代表专利链接。

删除旧候选上限/精读上限控件和 Deep Read 区域。

### 19.2 进度

SSE 必须显示：

- 当前阶段；
- 分片完成数；
- 搜索页数/查询数；
- 摘要成功/失败/待处理；
- 分类三个终态数量；
- 重试和熔断状态；
- 当前限制；
- 估算剩余任务数。

当 Run 进入 `WAITING_FOR_CREDENTIALS` 时，SSE 和历史页必须显示“已完成结果已保存”、
需重新提供的 Provider/Model 及继续入口，不得只显示为泛化的“暂停”或“失败”。

### 19.3 报告 Schema

新报告使用 `landscape-report/4.0.0`，不兼容旧报告。至少包含：

- scope snapshot；
- taxonomy version；
- retrieval coverage；
- publication/family/analysis unit counts；
- classification coverage；
- company/school views；
- category and Others views；
- time metrics and trends；
- representative patents；
- unresolved records；
- limitations；
- audit summary。

## 20. 安全与隐私

- 模型 API Key 只存在请求作用域/Run 内存租约，不落库、不写日志；
- SerpAPI Key 只从服务端私密文件读取；
- Prompt、错误和 Debug 不输出 Key 或最终带 Key URL；
- 摘要是外部不可信文本，不能把其中指令当成系统指令；
- 模型输出的公司、分类、专利号、Evidence 和 URL 全部程序校验；
- 外链仅允许 HTTPS Allowlist；
- 用户确认公司档案属于业务数据，API 不提供未授权的批量导出；
- 日志保存 ID、Hash、计数、耗时和错误码，不保存完整模型响应。

## 21. 故障与降级矩阵

| 故障 | 行为 |
|---|---|
| 公司扩展模型失败 | 返回历史档案和原始输入，等待用户手工确认 |
| 技术词扩展失败 | 返回原始词，等待用户手工补充 |
| 某检索查询失败 | 其他查询继续，报告显示公司/词组缺口 |
| Provider 分页中断 | 从最后成功页恢复 |
| 某摘要失败 | 重试后进入 `UNRESOLVED` |
| 批次 JSON 非法 | 保存合法项，非法项拆批重试 |
| 模型 429/5xx | 全局退避、遵守 Retry-After、任务留在队列 |
| 单件分类失败 | 最小单件重试后 `UNRESOLVED` |
| Others 命名失败 | 保留程序 Cluster ID 和确定性摘要 |
| 趋势叙述失败 | 保留程序指标和候选，生成确定性基础文本 |
| 服务重启 | 回收任务租约；非模型任务续跑，模型任务等待重新挂载凭证 |
| 模型凭证过期/丢失 | Run 进入 `WAITING_FOR_CREDENTIALS`，不丢失成功结果且不消耗业务重试 |
| Shard 失败 | 只恢复失败 Shard，不重做成功 Shard |
| 对账失败 | 全局 `FAILED`，不生成伪成功报告 |

## 22. 可观测性

每个 Run 记录：

- Provider 请求、页数、结果数、去重数和停止原因；
- 摘要状态和耗时分布；
- 每个模型 Agent 的请求数、输入/输出 Token、缓存命中、P50/P95；
- 当前/峰值并发、429、5xx、超时和重试；
- Batch 件数/Token 分布和拆批次数；
- `CLASSIFIED / OTHERS / UNRESOLVED` 数量；
- Family 合并、冲突和保守拆分数量；
- Shard 进度和恢复次数；
- 进程 RSS、数据库连接池和任务队列深度；
- 报告完成状态和限制。

所有指标以 `run_id`、`shard_id`、`task_type` 聚合，不使用专利全文作为标签。

## 23. 测试体系

### 23.1 单元测试

- Taxonomy Markdown 解析、重复和空层级；
- 公司名称规范化、版本、拒绝记忆和冲突；
- 双语技术词确认；
- 查询计划和多公司覆盖；
- Provider cursor/page 解析和停止原因；
- Publication、Application、Simple/Extended Family 边界；
- 摘要句 Evidence 和 Hash；
- Token 估算、动态装箱和确定性批次；
- 模型输出漏项、重复、未知 ID 和拆批；
- 分类三集合对账；
- Others 聚类成员不变量；
- 趋势门槛、统计和代表专利；
- URL Allowlist 和凭证脱敏。

### 23.2 合成规模 Fixture

| Fixture | 内容 |
|---|---|
| `LAND-050` | 三模式、公司记忆、基础分类 |
| `LAND-500` | 默认规模、动态批次、限流 |
| `LAND-1000` | 单次最大规模、内存和恢复 |
| `LAND-2500-SHARDED` | 三个分片、合并和局部失败 |
| `LAND-FAMILY` | 简单族、扩展族、分案、缺失 Family |
| `LAND-OTHERS` | 已分类、Others、Unresolved 严格边界 |

### 23.3 故障注入

至少注入：

- 5% Provider 超时；
- 429 + Retry-After；
- 5xx；
- 某页重复或空页循环；
- 摘要缺失；
- 模型漏一件、重复一件、未知类别、非法 Evidence；
- 批次响应截断；
- Worker 在写入前/后退出；
- 服务在每个主阶段重启；
- 重启后不提供 API Key，验证 Run 稳定停在 `WAITING_FOR_CREDENTIALS`；
- 重新挂载相同模型凭证，验证仅未完成任务续跑；
- 尝试挂载不同模型/能力配置，验证系统拒绝语义漂移；
- 数据库连接瞬断；
- 用户取消；
- 单个 Shard 永久失败。

### 23.4 真实验收

自动化回归只是门槛，真实系统运行是发布必需条件：

1. 使用真实 SerpAPI 和目标模型完成三种输入模式；
2. 完成至少三次不同输入的真实 `LAND-500`；
3. 完成至少一次真实 `LAND-1000`；
4. 验证分页确实超过第一页；
5. 验证模型并发、Token、429 和重试指标；
6. 在真实 Run 中途重启服务，验证等待凭证状态，重新挂载后成功恢复；
7. 抽样人工检查公司范围、Family、分类、Others 和代表专利；
8. 回查 PostgreSQL，证明集合和报告计数可复算；
9. 扫描数据库、报告、日志和 Git，真实 API Key 命中为 0。

## 24. 发布硬门

以下任一项不满足，不得合并为完成版本：

### 正确性

- Frozen Analysis Units 重复数为 0；
- 分类重复数和遗漏数为 0；
- Shard 合并重复数和遗漏数为 0；
- 未知专利、公司、类别、Evidence 和 URL 引用为 0；
- 报告所有核心计数可以从成员表复算。

### 稳定性

- `LAND-500` 连续三次完成；
- `LAND-1000` 至少一次完成；
- 每个主阶段重启后，无需凭证的任务自动恢复，需凭证的任务重新挂载后恢复；
- 注入瞬时失败不造成整 Run 重做；
- 单件永久失败产生限制，不造成数据丢失；
- 任务没有永久 `LEASED/RUNNING` 悬挂；
- 凭证丢失不能造成无限重试、误报 `FAILED` 或重做已成功任务；
- 1000 件运行期间不存在随总量线性保留全文对象的内存增长；
- 服务在报告完成后回收 Run 级内存。

### 安全

- API Key 不落库、不进日志、不进报告；
- Prompt 注入测试不能改变范围、类别身份和外链；
- 历史 Run 不能被新公司档案或 Taxonomy 修改。

### 产品

- 用户可完成公司/技术词审查；
- 三种模式报告结构正确；
- Others 与 Unresolved 清晰区分；
- 代表专利链接可在新页面打开；
- 所有限制在页面和下载报告中可见。

## 25. 实施工作包

每个工作包独立测试、独立验收；不得跨包顺手重构。

### WP-0：冻结 Fixture 和现状审计

- 为当前新产品 Schema 建立 Fixture；
- 建立旧代码调用图和待删除清单；
- 冻结基线测试和性能采样；
- 不修改运行行为。

完成门：Fixture、调用图、数据库表和旧 API 清单齐全。

### WP-1：Taxonomy Compiler

- 解析 `classify.md`；
- 生成稳定 ID、Hash 和 canonical JSON；
- 增加启动门禁和测试。

完成门：当前分类源的有效路径与 canonical Artifact 严格对账，无静默丢失；
重复/非法分类 fail closed。

### WP-2：公司档案和 Scope Draft

- 新表和 Repository；
- 公司历史记忆、模型增量建议、拒绝记忆；
- 技术词扩展；
- 用户审查/确认 API 和页面。

完成门：确认前不能创建正式 Run，旧快照不可变。

### WP-3：分页检索和冻结集合

- Provider Page 接口；
- SerpAPI 全分页；
- 多公司/多词查询计划；
- Scale Gate；
- Publication 去重和审计。

完成门：真实查询超过第一页且停止原因准确。

### WP-4：Family 和 Abstract

- 双层身份；
- 简单族保守合并；
- Family Conflict；
- 轻量摘要获取；
- 单件 checkpoint。

完成门：Family Fixture 和真实抽样通过，不请求 description/claims。

### WP-5：任务队列和模型调度器

- PostgreSQL lease queue；
- 全局并发、RPM/TPM/Token 限制；
- Batch Profile、动态装箱和拆批；
- Credential Lease、等待凭证状态和重启恢复。

完成门：故障注入不重复成功任务，批次失败可隔离到单件。

### WP-6：方向抽取和 Taxonomy 匹配

- Direction Record；
- Candidate Recall；
- 分类 Agent；
- 有界父类复核；
- 三集合对账。

完成门：500 Fixture 全部进入互斥终态，无未知 ID/Evidence。

### WP-7：Others

- 本地聚类；
- 质量门；
- 代表记录；
- 模型命名和确定性降级。

完成门：成员不变且无自动 Taxonomy 写回。

### WP-8：统计、趋势和代表专利

- 数据立方体；
- TrendCandidate；
- 有界叙述；
- 三模式视图；
- 外链。

完成门：统计可复算，趋势失败仍生成基础报告。

### WP-9：Graph、报告和页面切换

- 阶段协调；
- SSE；
- Report 4.0；
- 新页面；
- 删除 Deep Read 产品入口。

完成门：三模式真实小规模 Run 完成。

### WP-10：压力、恢复和旧代码删除

- LAND-500/1000/2500；
- 服务重启和故障注入；
- 内存与连接池检查；
- 删除旧实现、旧表引用和旧前端分支；
- 全量真实验收。

完成门：第 24 节全部通过，旧代码 import/call-site 为 0。

## 26. 开发纪律

- 每次只实施一个工作包中的一个可验收切片；
- 先写不变量和失败测试，再写实现；
- 不通过放宽校验来修复模型输出；
- 不用增加超时掩盖上下文或调度问题；
- 不为通过测试伪造 Provider 成功或模型成功；
- 自动化测试通过后必须进行真实 Run；
- 每个迁移必须幂等并带 checksum；
- 每个外部调用必须可定位到任务和输入 Hash；
- Git 历史承担开发日志，不新建追加式开发日志；
- 不提交 API Key、真实用户私密数据或完整模型响应。

## 27. 冻结决策

1. Scope Draft 与正式 Run 分离，模型扩展必须由用户确认；
2. 公司别名、法定名称和集团成员分开并版本化；
3. 搜索使用完整分页和明确 Scale Gate；
4. 首版 Publication 检索，Family 作为检索后保守去重层；
5. 只合并可确认简单族，不折叠扩展族；
6. 当前版本只读取标题和摘要；
7. `classify.md` 编译为不可变 Taxonomy Version；
8. 方向抽取与 Taxonomy 匹配采用动态有界批次；
9. 模型上下文来自可验证能力配置，不硬编码 100M；
10. Flash 负责 Bulk，Pro 负责冲突/趋势，单模型时允许合并；
11. 外部 Embedding 不是首版强依赖；
12. Others 成员由程序聚类，模型只命名；
13. 趋势指标由程序计算，模型只解释候选；
14. 默认 500、单次 1000、超过 1000 分片；
15. LangGraph 只做阶段协调，细粒度任务使用 PostgreSQL lease queue；
16. 新报告为 `landscape-report/4.0.0`，不兼容旧 Landscape 报告；
17. 稳定性发布门高于功能完成度，真实压力和恢复测试是必需项。
18. 旧 Landscape 配置、API、Run、报告和代码不兼容；新能力验收接管后直接删除对应旧链。

## 28. 外部接口事实基线

外部能力会变化，以下链接是文档建立时的实现依据，不是永久常量：

- [SerpAPI Google Patents API](https://serpapi.com/google-patents-api)：查询分页、`dups`
  与搜索返回字段；
- [SerpAPI Google Patents Details API](https://serpapi.com/google-patents-details-api)：
  `family_id`、worldwide applications、parent/child/priority 等详情字段；
- [EPO OPS 文档](https://link.epo.org/web/ops_v3.2_documentation_-_version_1.3.19_en.pdf)：
  INPADOC 扩展族服务的输入和返回边界；
- [DeepSeek API 模型与价格页](https://api-docs.deepseek.com/quick_start/pricing/?article_id=article_1779470751466_8)：
  文档建立时 V4 Pro/Flash 公开上下文能力。

实施 WP-3、WP-4 和 WP-5 时必须重新验证这些接口合同，将验证日期、
样例响应的脱敏 Fixture 和能力 Hash 保存到测试 Artifact；若官方行为改变，
先升级本文档版本，再改代码。

# 专利态势公司技术趋势 Agent 开发规格

状态：实施中（以当前源码和测试为准）

适用分支：`feature/agent-upgrade`

目标版本：Workflow `2.x`，Report Schema `landscape-report/2.0.0`

事实源：PostgreSQL

约束：本文件通过审查前，不修改生产业务代码

> 注：本文件最初是开发规格，现已进入实施阶段。若本文件中的“当前现状”
> 与源码不一致，以源码、迁移记录和对应测试为准；后续功能仍需按小功能提交。

## 0. 已实施的基础能力（2026-07-29）

- 主 Graph 已包含公司分析、跨公司趋势、覆盖审计和有限修复条件路由；
- 新 Run 不再执行 `CLUSTER_PATENTS`，该枚举和表仅用于历史读取兼容；
- 抓取和逐件精读对完整合格集合分批调度，`analysis_limit` 只作为批大小；
- Report 2.0 已输出合格集合、抓取、精读、公司归属和未覆盖公开号计数；
- 修复轮次使用独立任务键和追加式审计快照，可在中断后恢复。

## 1. 目标

本文件用于指导 Coding Agent 将现有“全局技术聚类”改造成：

1. 对本次检索范围内全部去重且合格的专利建立完整台账；
2. 对每件专利进行可审计的公司归属；
3. 在每家公司内部进行技术分类与技术方向总结；
4. 在公司分析通过覆盖校验后，归纳跨公司的共同方向、差异方向和有时间证据的技术趋势；
5. 使用 LangGraph 的动态分发、条件路由和定向修复，而不是继续使用固定直线流程；
6. 每完成一个可独立验收的小功能就提交一次 Git，并同步留下测试和开发记录。

本次不是简单修改聚类 Prompt。它同时涉及数据口径、持久化、公司归一、模型输出约束、Graph 控制流、报告契约和测试体系。

## 2. 基于实际源码的现状

当前真实执行链为：

```text
VALIDATE_SCOPE
→ PLAN_SEARCH
→ SEARCH_PUBLICATIONS
→ FILTER_AND_SELECT
→ FETCH_DETAILS
→ ANALYZE_PATENTS
→ CLUSTER_PATENTS
→ BUILD_REPORT
```

主要源码：

- `backend/landscape/workflow.py`：8 个步骤、LangGraph 构图和运行；
- `backend/landscape/execution.py`：步骤业务实现；
- `backend/landscape/search.py`：硬过滤、跨查询去重、排名和预算选择；
- `backend/landscape/analysis.py`：单件专利证据约束精读；
- `backend/landscape/clustering.py`：现有全局技术聚类；
- `backend/landscape/reporting.py`：Report JSON、Markdown、CSV；
- `deploy/rag/postgres-init/060_unified_runtime_schema.sql`：当前生产表。

已经确认的差距：

1. 当前 Graph 是循环生成的固定直线，没有条件边、`Send` 或修复回路。
2. `strict_filter_and_select()` 虽先去重，但随后受 `candidate_limit` 截断。
3. 抓取和精读又受 `analysis_limit` 限制。
4. `CLUSTER_PATENTS` 只消费成功精读的预算子集，不是完整合格集合。
5. 旧聚类 Prompt 明确要求按技术方案聚类，禁止按权利人分类。
6. `company_patent_counts` 只是临时统计，没有稳定 `company_id` 和逐专利归属决策。
7. `landscape_hits` 保存逐 Query 命中，同一专利可以重复，不能充当去重权威表。
8. 现有高阶聚类没有原文证据引用，趋势结论无法直接追溯。
9. `LandscapeWorkflowHarness.next_step()` 强制枚举顺序，步骤表也没有公司级 `task_key`。
10. 部分文档数据保存在进程内字典，动态并行前必须补齐持久化和恢复。

目标职责边界：

> 确定性代码负责范围、过滤、去重、公司归属约束、统计与完整性审计；模型负责证据范围内的技术语义归类和解释；LangGraph 负责动态公司任务、条件路由和有限修复。

## 3. 冻结集合口径

定义：

- `R`：Provider 返回的原始 Hit 多重集；
- `E`：通过公开号、公开日窗口和业务范围硬过滤的 Hit 多重集；
- `P`：`E` 按规范化公开号去重后的合格公开文本全集；
- `U`：`P` 按可靠 Family ID、申请号、公开号依次保守聚合后的身份组全集；
- `C`：完成公司归属后的全集，无法确认时进入 `UNKNOWN`；
- `F`：详情抓取成功集合；
- `A`：具有合格逐件分析及有效证据的集合；
- `T`：进入公司分类和跨公司归纳的集合。

必须满足：

```text
T ⊆ A ⊆ F ⊆ U
|C| = |U|
```

“所有去重且合格的专利”定义为：

> 所有 Provider 在本次查询与分页预算内返回，经过公开号、公开日期、业务范围等硬规则过滤，先形成唯一公开文本 `P`，再按可靠身份保守聚合形成的专利身份组 `U`。

其中上一句的“去重集合”分为两层：`P` 保存全部唯一合格公开文本，
`U` 保存供抓取、分析和公司统计使用的专利身份组。每个 `U` 必须同时保存
稳定的 `representative_publication_number` 和完整的
`member_publication_numbers`，不得让其他同族公开文本静默消失。

边界：

- “所有”不等于全球专利数据库中的所有专利；
- Provider 超时、配额、分页和 `per_query_limit` 必须进入限制说明；
- 只有 Provider 返回一致且非空的 Family ID 时才确认专利族；其次允许同一申请号的 A/B 阶段公开文本聚合；
- Family/Application 身份缺失时按规范化公开号保守分开，不使用标题相似度推断合并；
- 同一公开号或申请号出现冲突 Family ID 时禁止传递合并，记录 `identity_conflict_count` 并保守隔离；
- 代表公开号选择必须与 Provider、Query 和返回顺序无关：优先 A 类公开文本，再按规范化公开号排序；
- 不允许从 `U` 静默抽样后声称是整体趋势；
- 成功报告要求 `A = U`；
- 若 `A != U`，Run 必须是 `COMPLETED_WITH_LIMITATIONS`，报告列出 `U-A` 和 `A/U`；
- 若设置硬上限，超过时必须显式拒绝、审批或输出受限结论。
- 报告必须分别展示 `|P|`、`|U|`、确认 Family、申请号聚合、公开号保守项和身份冲突数。

固定记录：

```text
raw_hit_count
eligible_hit_count
unique_eligible_count
company_assigned_count
fetch_attempted_count
fetch_succeeded_count
analysis_attempted_count
analysis_succeeded_count
company_classified_count
unclassified_count
```

## 4. 输入契约

保留现有 `LandscapeScope`，新增策略：

```json
{
  "technology_direction": "数据中心液冷",
  "competitors": [
    {"name": "公司主名称", "aliases": ["已确认中文名", "已确认英文名"]}
  ],
  "publication_start": "2025-01-01",
  "publication_end": "2025-12-31",
  "budget": {
    "candidate_limit": 100,
    "analysis_limit": 20,
    "per_query_limit": 50
  },
  "analysis_policy": {
    "coverage_mode": "ALL_ELIGIBLE",
    "unknown_assignee_policy": "KEEP_AS_UNKNOWN",
    "joint_assignee_policy": "PRIMARY_WITH_CO_ASSIGNEES",
    "max_eligible_patents": 200,
    "fetch_batch_size": 10,
    "analysis_concurrency": 4,
    "company_analysis_concurrency": 3,
    "max_repair_rounds": 1,
    "minimum_patents_for_time_trend": 3,
    "time_bucket": "QUARTER"
  }
}
```

预算迁移：

- `per_query_limit` 继续限制 Provider 单次返回量；
- `candidate_limit` 改为显式安全上限，不再表示抽样量；
- `analysis_limit` 不再决定成员，兼容期可作为批大小；
- 并发数只影响资源，不影响集合成员；
- 超过 `max_eligible_patents` 由策略节点明确拒绝、待审批或受限运行。

公司归属：

- 每件专利必须有且只有一个 `primary_company_id`；
- 共同申请人保存为辅助关系，不得导致全局重复计数；
- 竞争对手模式优先使用用户确认的主名称和别名；
- 纯技术模式使用原始权利人进行规范化；
- 模型只能提出建议，不能绕过确定性校验直接合并；
- 歧义、冲突或缺失进入 `UNKNOWN`/`REVIEW_REQUIRED`，不得猜测。

## 5. 输出契约

目标 Report 2.0 核心结构：

```json
{
  "schema_version": "landscape-report/2.0.0",
  "coverage": {
    "unique_eligible_count": 12,
    "company_assigned_count": 12,
    "fetch_succeeded_count": 11,
    "analysis_succeeded_count": 10,
    "company_classified_count": 10,
    "analysis_coverage": 0.833333,
    "unclassified_publications": ["P11", "P12"]
  },
  "companies": [
    {
      "company_id": "CO-A",
      "display_name": "公司 A",
      "observed_assignees": ["Company A Ltd.", "A公司"],
      "eligible_patent_count": 6,
      "analyzed_patent_count": 5,
      "overall_summary": "公司级总结",
      "technology_directions": ["冷板换热", "冷却液控制"],
      "technology_categories": [
        {
          "category_id": "TC-A-01",
          "name": "冷板换热结构",
          "summary": "证据约束的分类总结",
          "keywords": ["冷板", "微通道"],
          "publication_numbers": ["P1", "P2"],
          "evidence_ids": ["EV-1", "EV-2"]
        }
      ],
      "limitations": []
    }
  ],
  "cross_company_analysis": {
    "overall_summary": "本时间窗内各公司的整体技术方向",
    "common_directions": ["多家公司共同布局的方向"],
    "differentiated_directions": ["公司间差异化方向"],
    "trends": [
      {
        "trend_id": "TR-01",
        "name": "冷却液控制相关公开增加",
        "summary": "程序统计和证据支持的解释",
        "direction": "GROWING",
        "company_ids": ["CO-A", "CO-B"],
        "publication_numbers": ["P1", "P7"],
        "evidence_ids": ["EV-1", "EV-7"],
        "time_basis": {"start": "2025-01-01", "end": "2025-12-31", "bucket": "QUARTER"}
      }
    ],
    "limitations": []
  },
  "audit": {
    "decision": "PASS",
    "coverage_ratio": 1.0,
    "invented_publications": [],
    "duplicate_memberships": [],
    "missing_publications": [],
    "invalid_evidence_refs": []
  }
}
```

约束：

- 报告同时展示 `U` 和 `A`，失败专利不能消失；
- 每家公司内，属于该公司的 `A` 必须恰好进入一个主技术分类；
- 公司总结只能引用该公司专利；
- 跨公司结论只能引用已验证公司分析中的专利和证据；
- “增长、下降、转向、加速”等趋势至少需要两个时间桶、两件不同专利；
- 时间计数、比例和斜率由程序计算，模型只解释；
- 时间证据不足时只能输出“观察到的技术方向”；
- 单家公司不生成“跨公司趋势”；
- `UNKNOWN` 也是可审计分组。

## 6. 领域模型与硬校验

新增模型：

```text
NormalizedCompany
CompanyAssignment
CompanyTechnologyCategory
CompanyTechnologyProfile
CrossCompanyTrend
CrossCompanyTrendAnalysis
LandscapeCoverageAudit
```

关键字段：

- 公司：`company_id/canonical_name/aliases/raw_names/resolution_source/confidence`；
- 归属：`publication_number/primary_company_id/observed_assignee/matched_alias/co_assignees/status`；
- 分类：`category_id/company_id/name/summary/keywords/publication_numbers/evidence_ids`；
- Profile：专利数、分类、整体总结、方向、证据、限制；
- Trend：方向枚举、公司、公开号、证据和时间基础；
- Audit：`PASS/REPAIR/LIMITED/FAIL`、缺失、重复、虚构和修复目标。

硬校验：

1. Assignment 公开号必须属于 `U`；
2. 每件 `U` 恰好一个 primary assignment；
3. confirmed alias 准确，别名冲突 fail closed；
4. 每家公司主分类成员集合恰好等于该公司的 `A`；
5. 分类不得包含其他公司或未知公开号；
6. 所有证据必须存在、哈希有效，并属于对应专利；
7. 公司级结论不得引用公司集合外证据；
8. 跨公司趋势必须形成公司—专利—证据有效链；
9. 模型不得新增或修改公开号、公司 ID、日期和程序统计；
10. 单件专利采用确定性单分类。

## 7. PostgreSQL 方案

新增：

```text
deploy/rag/postgres-init/070_landscape_company_analysis.sql
```

新增表：

- `landscape_candidates`：过滤、去重后的权威集合 `U`；
- `landscape_companies`：Run 内 canonical company；
- `landscape_document_companies`：文档与主/共同公司关系；
- `landscape_company_categories`；
- `landscape_company_category_members`；
- `landscape_company_profiles`；
- `landscape_cross_company_trends`；
- `landscape_insight_evidence`：CATEGORY、PROFILE、TREND 到原始 Evidence 的关系。

`landscape_candidates` 至少包含：

```text
run_id, document_id, publication_number, normalized_key, rank,
decision, metadata_json, content_hash, created_at
PRIMARY KEY(run_id, document_id)
UNIQUE(run_id, publication_number)
```

持久化规则：

- 同一公司内一件专利只进入一个主分类；
- 所有写入沿用“内容哈希一致则幂等，不一致则拒绝覆盖”；
- PostgreSQL 是唯一生产事实源；
- 旧数据无需回填，旧 Report 继续按 1.1 读取，新 Run 只写 2.0；
- 不恢复 SQLite 运行时；
- 测试仓储迁移另作独立提交。

公司级任务日志为 `landscape_steps` 增加：

```text
task_key = "__main__" | company_id | publication_number
UNIQUE(run_id, step_name, task_key, attempt)
```

Graph 决定下一节点；PostgreSQL 按依赖、`task_key` 和 attempt 记录。成功公司任务恢复时不得再次调用模型，失败分支不得覆盖其他公司结果。

## 8. 目标 LangGraph

```text
START
  ↓
VALIDATE_SCOPE
  ↓
PLAN_SEARCH
  ↓
SEARCH_PUBLICATIONS
  ↓
FILTER_AND_DEDUPLICATE
  ├─ U 为空 ───────────────────────→ BUILD_EMPTY_REPORT
  ↓
NORMALIZE_COMPANIES
  ├─ 需人工确认 ───────────────────→ REVIEW_COMPANY_ASSIGNMENTS
  ↓
FETCH_ELIGIBLE_DOCUMENTS
  ↓
ANALYZE_ELIGIBLE_PATENTS
  ↓
PREPARE_COMPANY_BATCHES
  ↓
[Send: ANALYZE_COMPANY × N]
  ↓
REDUCE_COMPANY_ANALYSES
  ↓
ANALYZE_CROSS_COMPANY_TRENDS
  ↓
VERIFY_COVERAGE
  ├─ PASS ─────────────────────────→ BUILD_REPORT
  ├─ REPAIR ───────────────────────→ REPAIR_GAPS ─→ VERIFY_COVERAGE
  ├─ LIMITED ──────────────────────→ BUILD_LIMITED_REPORT
  └─ FAIL ─────────────────────────→ FAIL_RUN
```

节点职责：

- 过滤节点建立完整 `U`，不抽样；
- 公司节点确定性别名优先，模型建议必须校验；
- 抓取和精读分批处理全部目标并持久化状态；
- Batch 节点只做确定性分组；
- 公司节点只消费本公司专利和证据；
- Reducer 顺序无关、重复 delivery 幂等；
- 跨公司节点消费已校验公司结果与程序时间统计；
- Audit 为纯代码规则；
- Repair 只修明确缺口，不重跑整链；
- Limited 报告诚实披露覆盖率。

LangGraph：

- `add_conditional_edges` 用于空结果、人工确认和 Audit 路由；
- `Send` 用于公司级并行；
- Reducer 只合并控制信息，业务结果写 PostgreSQL；
- `RetryPolicy` 只处理瞬时错误；
- 修复轮数有上限。

LangChain：

- 继续复用 `StructuredModelClient + ChatOpenAI`；
- 不引入 `AgentExecutor`、Memory、Retriever 或 Tool Agent；
- Prompt、Schema、集合和证据校验仍由项目控制。

## 9. Graph State

```python
class LandscapeAgentState(TypedDict):
    run_id: str
    current_stage: str | None
    unique_eligible_count: int
    company_ids: list[str]
    completed_company_ids: Annotated[list[str], operator.add]
    repair_round: int
    max_repair_rounds: int
    audit_decision: Literal["PENDING", "PASS", "REPAIR", "LIMITED", "FAIL"]
    repair_targets: list[str]
    limitations: Annotated[list[dict], operator.add]

class CompanyAnalysisTaskState(TypedDict):
    run_id: str
    company_id: str
    publication_numbers: list[str]
```

约束：

- State 不保存全文、证据包和全部分析 JSON；
- 节点通过 ID 从 PostgreSQL 读取事实；
- Graph State 不是第二套业务数据库；
- 当前不强制重引入 LangGraph Checkpointer；
- 若以后增加 Checkpointer，只保存控制游标，不保存凭证和大 Payload。

## 10. 模拟测试集

目录：

```text
backend/tests/fixtures/landscape_agent/
  scopes.json
  provider_hits.jsonl
  fetched_documents.jsonl
  alias_registry.json
  patent_analysis_outputs.jsonl
  company_profile_outputs.jsonl
  trend_outputs.jsonl
  graph_cases.json
  expected/
```

所有测试固定日期、UUID 和模型输出，不访问网络。Provider 使用内存实现，模型按 `agent_name + case_id` 返回 scripted JSON。

基准场景 `BASE-01`：

- 时间窗：`2026-04-01..2026-06-30`；
- 技术方向：数据中心液冷；
- 12 条原始 Hit；
- 包含跨 Query/Provider 的同一公开号、Huawei 中英文、Vertiv 中英文、Meta、Metallurgy、无权利人、窗口外、无公开号、非法日期和日期缺失。

预期：

```text
raw_hit_count = 12
eligible_hit_count = 8
|U| = 7
duplicate_merged = 1
Huawei = 2
Vertiv = 2
Meta = 1
Metallurgy Systems = 1
UNKNOWN = 1
```

若全部成功，则 `F = A = T = U = 7`。

首批至少 42 个 case：

- 范围、过滤和去重：12；
- 公司归一：10；
- 公司技术分类：8；
- 趋势和证据：8；
- 失败和 Graph 路由：至少 6。

必须覆盖日期边界、跨源重复、同族不误折叠、`Meta`/`Metallurgy`、别名冲突、共同申请人、UNKNOWN、漏件/重复/串公司、证据串件、单时间桶、fan-out 单支失败、重启恢复和修复耗尽。

## 11. 验收门槛

硬门槛，任一失败不得合并：

```text
过滤 precision/recall = 100%
日期边界正确率 = 100%
公开号去重准确率 = 100%
未知公开号引入数 = 0
同族误折叠数 = 0
confirmed alias fixture 准确率 = 100%
危险公司误合并数 = 0
company assignment coverage = 100%
成功 Run 的 analysis coverage = 100%
公司主分类 coverage = 100%
duplicate/missing/unknown membership = 0
证据有效率、归属正确率、hash 校验率 = 100%
无双时间桶却输出方向性趋势 = 0
程序统计与报告数字不一致 = 0
Graph 预期路径覆盖 = 100%
恢复后重复外部调用 = 0
JSON/Markdown/CSV 交叉一致率 = 100%
Manifest 完整性 = 100%
敏感凭证落盘 = 0
```

模型质量：

- 公司归一 macro-F1 ≥ 0.98，false merge ≤ 0.5%；
- 公司技术主类 macro-F1 ≥ 0.85；
- 公司总结证据支持率 ≥ 0.90；
- 跨公司趋势正确率 ≥ 0.85；
- 固定输入运行 3 次，结构成员和引用集合一致率 100%；
- 虚构公开号、公司、日期、计数按硬失败处理。

测试分层：

- PR：Schema、Validator、Normalizer、Route、scripted model，目标 `<60s`；
- 模块：PostgreSQL、恢复、完整报告，目标 `<5min`；
- Canary：真实 Provider/模型，仅做漂移观察；
- 新模块 line coverage ≥ 90%，branch ≥ 85%；
- Validator 与路由 branch = 100%。

Graph 契约：

- 稳定 Node ID 和拓扑快照；
- `U=∅` 不调用详情和模型；
- `Send` 数量等于公司 bucket 数；
- 公司任务只收到本公司专利和只读证据引用；
- Reducer 顺序无关且幂等；
- Audit 决策由代码完成；
- Retry 只重跑失败节点/公司；
- 成功公司不得重复产生模型费用。

## 12. Git 留痕和提交计划

强制规则：

1. 一个提交只解决一个可描述、可回滚的问题；
2. 实现、测试、Fixture 和开发日志在同一提交；
3. 本切片测试通过后才能提交；
4. 每次提交更新 `development/agent-upgrade/CHANGELOG.md`；
5. Landscape 决策同时追加 `development/landscape/development-log.md`；
6. 禁止最后一次性补测试；
7. 禁止为模型样例放宽硬 Validator；
8. 不使用 `--no-verify`；
9. 记录提交 SHA、测试命令、结果和限制；
10. 上一个提交未验收，不开始下一个；
11. 不压平历史，不使用 `git reset --hard`；
12. 推送前运行 Landscape 全量回归和 `git diff --check`。

提交顺序：

1. `docs(landscape): define company trend agent contract`
2. `test(landscape): add synthetic company trend fixtures`
3. `feat(landscape): add company trend schemas`
4. `db(landscape): add canonical candidate persistence`
5. `feat(landscape): preserve complete eligible deduplicated set`
6. `feat(landscape): assign confirmed companies deterministically`
7. `feat(landscape): validate assignee normalization suggestions`
8. `db(landscape): persist company assignments and analyses`
9. `feat(landscape): fetch all eligible patents in resumable batches`
10. `feat(landscape): analyze every fetched eligible patent`
11. `feat(landscape): build deterministic company batches`
12. `feat(landscape): classify technologies within one company`
13. `feat(landscape): aggregate company technology profiles`
14. `feat(landscape): synthesize evidence-bound company trends`
15. `feat(landscape): audit analysis coverage and evidence`
16. `refactor(landscape): support keyed workflow task attempts`
17. `feat(landscape): fan out company analysis with langgraph`
18. `feat(landscape): route audit gaps through bounded repair`
19. `feat(report): publish landscape company trend schema v2`
20. `feat(frontend): render company technology trends`
21. `test(landscape): cover company trend workflow end to end`
22. `docs(landscape): record company trend agent delivery`

不要一开始就引入 `Send`。先稳定全集、公司归属、持久化、单公司分析和幂等恢复，否则动态并行只会放大进程内状态问题。

## 13. 分阶段合并门

### Gate A：数据口径

- `U` 有权威持久化；
- 不再受旧 `analysis_limit` 静默抽样；
- 公司归属覆盖 `U`；
- BASE-01 全部通过。

### Gate B：分析能力

- 全量抓取和精读可恢复；
- 单公司分类恰好覆盖该公司的 `A`；
- 公司总结和证据链通过 Validator；
- 失败进入受限口径。

### Gate C：Agent Graph

- keyed task attempt 可恢复；
- 公司 fan-out 正确；
- 条件边和修复回路有最大次数；
- 取消、超时、恢复和单分支失败测试通过。

### Gate D：产品输出

- Report 2.0、Markdown、CSV 和前端一致；
- 所有数字由程序计算；
- 完整/受限报告都通过交叉一致性检查；
- Landscape 全量回归、PostgreSQL 集成和真实 Canary 完成。

## 14. 审查决策

开发前需要确认：

1. 去重键采用规范化公开号，不按专利族折叠；
2. 成功报告要求 `A=U`，否则只能是受限报告；
3. 旧 `candidate_limit/analysis_limit` 不再用于静默抽样；
4. 一件专利一个 primary company，共同申请人作为辅助关系；
5. 无法确认的公司进入 `UNKNOWN`，模型不能擅自合并；
6. 只有满足时间证据门槛时才能输出增长、下降和转向；
7. PostgreSQL 是唯一生产事实源，`070` 不回填旧 Run；
8. Report 2.0 与旧 1.1 可并存读取，新 Run 只写 2.0；
9. 先完成数据和证据闭环，再实现 LangGraph `Send`；
10. 每个小功能独立提交，且携带测试与开发日志。

十项通过审查后，从提交 2 开始进入业务开发。

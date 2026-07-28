# Agent 升级开发记录

本文件采用追加方式。新的记录写在最上方，不删除历史决策。

## 2026-07-28 — landscape-append-only-repair-snapshots

### 已完成

- 新增 PostgreSQL migration `075_landscape_repair_snapshots`，为公司 Profile 和跨公司趋势建立按 repair round 追加的快照表。
- 修复快照同一 `(run_id, repair_round, entity)` 内容可幂等重放，内容不同一律拒绝覆盖。
- 读取公司画像和跨公司趋势时自动叠加最新修复版本；基础快照、分类明细、Trend 明细和既有 Audit 历史不被修改。

### 验证

- PostgreSQL Snapshot/Repair 聚焦 19 项、Landscape 全量 154 项测试通过。
- Python compileall 与 `git diff --check` 通过；无真实模型调用。

## 2026-07-28 — landscape-bounded-repair-plan

### 已完成

- 新增纯确定性 `LandscapeRepairPlan`：只消费 Coverage Audit 的 `REPAIR` 目标，不会重新检索或扩张冻结范围。
- 支持 `FETCH / ANALYZE / CLASSIFY / PROFILE / TREND` 目标，自动推导受影响公司的画像重建及跨公司趋势重建依赖。
- 对非 REPAIR 决策、非法格式、未知动作、未知公司、超出 U 集合的专利目标和不完整公司分区 fail closed。

### 验证

- Repair Plan/Audit 聚焦 7 项、Landscape 全量 152 项测试通过。
- Python compileall 与 `git diff --check` 通过；无真实模型调用。

## 2026-07-28 — landscape-report-coverage-audit

### 已完成

- Report JSON 新增结构化 `company_trend_coverage`，并在 Summary 暴露审计决策与覆盖率。
- Markdown 与前端新增“公司趋势覆盖审计”，展示 PASS/LIMITED、覆盖率、修复轮次和审计限制；历史 Run 明确标记未记录该数据。
- VERIFY_COVERAGE 的 Stage Result 公开已验证的 `repair_targets`，为后续有限修复路由提供控制输入。

### 验证

- Report/Frontend/Company Execution 聚焦 13 项、Landscape 全量 149 项测试通过。
- Python compileall 与 `git diff --check` 通过；无真实模型调用。

## 2026-07-28 — landscape-company-trend-report-v2

### 已完成

- 新 Run 的 Report Schema 升级为 `landscape-report/2.0.0`，删除 `clusters` 与 `summary.cluster_count`。
- Report JSON/Markdown/CSV 的主叙事为公司专利族统计、公司技术画像、跨公司趋势和逐件证据化精读。
- Execution Service 不再构造旧聚类模型或调用其 Prompt，`BUILD_REPORT` 不读取旧 Cluster Snapshot。
- 前端不为新 2.0 报告展示技术聚类；历史 1.x 报告仅在自身含有旧数据时显示“历史技术聚类”。

### 验证

- Report/Frontend/Workflow 聚焦 13 项、Landscape 全量 149 项测试通过。
- Python compileall 与 `git diff --check` 通过；无真实模型调用。

## 2026-07-28 — landscape-main-graph-retire-legacy-cluster

### 已完成

- 新建公司趋势 Run 的活动步骤、进度和完成门不再包含旧 `CLUSTER_PATENTS`。
- Coverage Audit 的 PASS/LIMITED 条件边直接进入 `BUILD_REPORT`，不再额外调用一次旧聚类模型。
- 保留旧 Step 枚举、聚类持久化和报告读取能力，历史 Run 若已有 Cluster Snapshot 仍可恢复展示。
- 新 Run 没有 Cluster Stage Result 时，Report 按公司画像与跨公司趋势正常生成。

### 验证

- Workflow 聚焦 6 项、Landscape 全量 149 项测试通过。
- Python compileall 与 `git diff --check` 通过；无真实模型调用。

## 2026-07-28 — landscape-company-trend-report-v1-3

### 已完成

- Report Schema 升级到 `landscape-report/1.3.0`。
- 报告 JSON 新增 `company_profiles` 与 `cross_company_analysis`，保留 Profile 分类成员、Evidence ID、趋势方向、公司、公开号和时间基础。
- Markdown 新增“公司技术画像”和“跨公司整体技术趋势”章节。
- 前端新增公司画像卡片、跨公司趋势卡片和趋势数量指标；旧技术聚类暂时保留用于兼容历史报告。

### 验证

- Report/Frontend 聚焦 7 项、Landscape 全量 149 项测试通过。
- Python compileall 与 `git diff --check` 通过；无模型调用。

## 2026-07-28 — landscape-main-graph-coverage-routing

### 已完成

- 主 Workflow 新增 `VERIFY_COVERAGE`，从 PostgreSQL 重建 U/F/A/T、公司归属、Profile、Trend 和 Evidence 集合。
- Audit round 0 原子持久化并可恢复；PASS 与 LIMITED 通过 async 条件边继续，FAIL 以确定性错误终止 Run。
- 当前明确设置 `max_repair_rounds=0`：尚未实现修复执行器前，缺口输出 LIMITED，不伪装成已修复。
- LIMITED 的审计限制进入最终 Run limitations，从而形成 `COMPLETED_WITH_LIMITATIONS`。
- 主 Graph 端到端测试实际执行全部节点和条件边，验证短事件循环可正常结束。

### 验证

- Audit/Graph 聚焦 12 项、Landscape 全量 149 项测试通过。
- Python compileall 与 `git diff --check` 通过；无模型 API Key。

## 2026-07-28 — landscape-main-graph-cross-company-trends

### 已完成

- 主 Workflow 新增 `ANALYZE_CROSS_COMPANY_TRENDS`，位于公司 fan-out 完成之后。
- 趋势输入从 PostgreSQL Profile、Patent Analysis、Fetched Document 和冻结 Scope 重建，Graph State 不保存业务正文。
- 使用公开日生成程序拥有的季度 Time Basis；缺失或非法日期直接 fail closed。
- 已存在趋势快照时重新校验公司—专利—Evidence 链、时间范围和方向阈值后跳过模型。
- 单公司场景确定性保存零趋势受限结果，并可恢复，无需模型调用。

### 验证

- 趋势执行/主 Workflow 聚焦 10 项、Landscape 全量 147 项测试通过。
- Python compileall 与 `git diff --check` 通过；无真实模型调用。

## 2026-07-28 — landscape-main-graph-company-stage

### 已完成

- 主 Workflow 在 `ANALYZE_PATENTS` 后新增 `ANALYZE_COMPANIES` 节点，并纳入顺序、attempt、进度和完成门。
- 主节点从 PostgreSQL Assignment 与成功 Patent Analysis 重建公司批次，只分发拥有成功分析的非空公司。
- Runtime 显式构造并绑定公司 LangGraph fan-out；Execution Service 防止重复绑定。
- 主节点输出只保存 company IDs 和计数，公司 Profile 继续由各 keyed 子任务写入 PostgreSQL。

### 验证

- 公司主节点/fan-out/Harness 聚焦 12 项、Landscape 全量 146 项测试通过。
- Python compileall 与 `git diff --check` 通过；无真实模型调用。

## 2026-07-28 — landscape-company-langgraph-fanout

### 已完成

- 新增独立公司分析 LangGraph 子图，通过 `Send` 为稳定排序后的每个 company ID 创建动态任务。
- 每个分支以 company ID 作为 PostgreSQL `task_key`，调用已接入的公司 Classification/Profile 执行边界。
- 成功任务恢复时直接跳过；单分支失败保留其他公司的成功结果，重启只推进失败分支的新 attempt。
- Graph State 只聚合完成 company ID，不保存分类、Profile、全文或 Evidence 大对象。
- 定位并规避 LangGraph 1.2.x 的短事件循环兼容问题：异步图的条件路由必须同样使用 async callable。

### 验证

- fan-out 聚焦 3 项、Landscape 全量 145 项测试通过。
- Python compileall 与 `git diff --check` 通过；脚本执行器无模型 API Key。

## 2026-07-28 — landscape-family-aware-ranking

### 已完成

- 候选去重从“仅规范化公开号”升级为可靠身份聚合：优先使用 Provider 返回的 `family_id`，其次使用申请号，最后以规范化公开号作为保守单件候选。
- 明确区分原始命中、合格命中、唯一公开文本和唯一专利族；同一申请的 A1/B2、以及已确认属于同族的跨法域公开文本不再重复进入候选集合。
- 排序不再依赖批次内最大 RRF。固定排名分按 `1 - (best_rank - 1) / (per_query_limit - 1)` 计算，避免候选集合变化导致同一专利分数漂移。
- 新增非线性同族布局分：0/1/2/3–4/5–7/8+ 个可核验法域分别记 0/0.2/0.4/0.6/0.8/1.0；法域同时从 Provider 的 `country_status` 和已确认同族公开号推导，不直接奖励 A1/B2 等同法域文本数量。
- 按场景分配基础权重：技术模式为技术匹配 55%、同族布局 25%、固定排名 15%、时间活跃度 5%；友商模式为同族布局 55%、固定排名 30%、时间活跃度 15%；组合模式为技术匹配 45%、同族布局 35%、固定排名 15%、时间活跃度 5%。
- 只有实际观察到多个检索式或多个 Provider 命中时才分别给予最多 0.03/0.02 的确认加分；单 Provider、单检索式不会凭常量获得“覆盖分”。
- 合格条件继续与排序解耦：公开号合法、公开日在指定闭区间内，友商/组合模式还必须命中用户确认的公司名称或别名；技术相关度和综合分只排序，不删除低分合格专利族，以保持高召回。
- 过滤阶段不会为了补 Family ID 逐件抓取详情或全文。仅当公开日缺失时执行原有的有界详情补全，并顺便复用申请号/Family ID；身份仍未知时保守分开，避免错误合并和再次出现“去重阶段抓全文卡死”。
- 代表公开号改为稳定选择：优先 A 类公开文本，再按规范化公开号排序，不受 Query、Provider 或返回顺序影响。
- 同一公开号/申请号出现冲突 Family ID 时撤销不可靠身份并保守隔离，禁止一次错误映射把两个专利族传递合并。
- Coverage 拆分确认 Family、申请号聚合、公开号保守项和身份冲突数；开发规范同步将唯一公开文本 `P` 与分析身份组 `U` 分层。
- 报告 Schema 升级为 `landscape-report/1.2.0`，前端分别展示唯一专利族、公开文本、同族布局分和固定排名分，并保留精读失败统计。

### 验证

- 新增同族跨公开号聚合、A1/B2 合并、单 Provider 零共识加分、同族布局优先、固定排名稳定性和过滤阶段不抓全文等回归用例。
- Landscape 全量 142 项测试通过。

## 2026-07-28 — landscape-company-execution-recovery

### 已完成

- Execution Service 新增单公司分析入口，从持久化 Assignment 与 Patent Analysis 重新构造确定性公司批次。
- 多专利公司依次执行技术分类和公司 Profile；结果经既有硬校验后交给 PostgreSQL Profile Repository 原子保存。
- 重启时优先读取已完成 Profile，成功恢复不再调用分类或 Profile 模型。
- 恢复结果仍会与当前公司批次、公开号和 Evidence 重新校验；缺件、串件或损坏结果 fail closed。
- 生产 Runtime 显式注入 PostgreSQL Profile Repository。

### 验证

- 公司执行/恢复聚焦 3 项、Landscape 全量 135 项测试通过。
- Python compileall 与 `git diff --check` 通过；测试使用脚本模型，无 API Key。

## 2026-07-28 — landscape-keyed-step-harness

### 已完成

- Workflow Harness 的 start/complete/fail/latest 全部支持显式 `task_key`，默认主流程仍使用 `__main__`。
- Attempt 计数按 `(run_id, step_name, task_key)` 隔离；不同公司可独立从 attempt 1 开始并分别恢复。
- 主流程继续执行严格顺序与唯一 Stage Result；fan-out 子任务只写自身 Step 输出和业务结果，不争抢主流程 Stage Result。
- 单个子任务重试耗尽不会提前终止整个 Run，保留给 Reducer 和 Coverage Audit 做全局判断。
- SQLite 仅同步测试仓储的表契约，生产运行时仍以 PostgreSQL `074` 为事实源。

### 验证

- Workflow/测试仓储聚焦 13 项、Landscape 全量 132 项测试通过。
- Python compileall 与 `git diff --check` 通过；无模型调用或 API Key。

## 2026-07-28 — landscape-keyed-step-schema

### 已完成

- 新增 PostgreSQL `074_landscape_keyed_steps` 迁移，为 `landscape_steps` 增加非空 `task_key`，既有记录自动归入 `__main__`。
- Step Attempt 唯一键升级为 `(run_id, step_name, task_key, attempt)`，公司级和专利级 fan-out 不再互相占用尝试次数。
- 新增 task lookup 索引，并将已有数据卷显式迁移链和应用启动版本门提升到 `074`。
- 迁移可重复执行；保留原有步骤记录，不删除业务数据。

### 验证

- PostgreSQL Schema/部署基础设施 28 项测试通过。
- Landscape PostgreSQL Repository 17 项、Landscape 全量 130 项测试通过。
- Python compileall 与 `git diff --check` 通过；本提交无模型调用或 API Key。

## 2026-07-28 — landscape-company-profile-persistence

### 已完成

- 新增公司 Profile 的 PostgreSQL 多表原子写入与读取。
- 写入前从数据库重新验证本公司 PRIMARY 归属、成功 Analysis、Candidate document ID 和 Evidence Owner。
- Categories、Members、Category Evidence、Profile Snapshot 与 Company Manifest 在同一事务提交。
- Profile 必须恰好覆盖该公司全部成功 Analysis；每件分类成员必须贡献自身已持久化 Evidence。
- 幂等重放同时比较 Profile 哈希、Manifest、Category 哈希、成员映射和 Evidence 映射；半套数据或旁路修改 fail closed。

### 验证

- Landscape PostgreSQL Repository 16 项测试通过。
- Landscape 全量 129 项测试、Python compileall 与 `git diff --check` 通过。
- 本提交只涉及确定性持久化，无模型调用或 API Key。

## 2026-07-28 — landscape-trend-audit-persistence

### 已完成

- 新增跨公司 Analysis 的 PostgreSQL 原生写入与读取，快照和逐 Trend 行在同一事务内提交。
- 合法零趋势 Analysis 也拥有持久化完成快照，可在恢复时直接跳过模型。
- 同内容重放幂等；快照、Trend 行或内容哈希不一致时拒绝覆盖并报告损坏。
- Coverage Audit 按 `(run_id, repair_round)` 追加保存，同一轮结果不可变，不同轮次保留完整决策历史。
- 所有写入先锁定 Run 行，未知 Run 明确失败。

### 验证

- Landscape PostgreSQL Repository 12 项测试通过。
- Landscape 全量 125 项测试、Python compileall 与 `git diff --check` 通过。
- 本提交不调用模型；公司 Profile 多表持久化留在下一独立提交。

## 2026-07-28 — landscape-company-result-manifests

### 已完成

- 新增 PostgreSQL `073_landscape_company_result_manifests` 迁移。
- 公司分析 Manifest 冻结 company/category/member 数量与集合哈希，为公司级幂等恢复提供完成标志。
- 新增跨公司 Analysis 快照表，即使合法结果包含零条 Trend，也能区分“已执行”与“尚未执行”。
- 新增按 `repair_round` 保存的 Coverage Audit 表，保留 PASS/REPAIR/LIMITED/FAIL 每轮决策。
- 将 `073` 接入已有数据卷显式迁移链，并把 Landscape 启动版本门提升到 `073`。

### 验证

- PostgreSQL Schema、部署迁移与 Landscape Repository 聚焦测试 37 项通过。
- Landscape 全量 123 项测试、Python compileall 与 `git diff --check` 通过。
- 本提交只建立持久化 Schema，Repository 写入在下一独立提交实现。

## 2026-07-28 — landscape-coverage-evidence-audit

### 已完成

- 新增纯程序 Coverage Audit，统一验证 `T ⊆ A ⊆ F ⊆ U`、PRIMARY 公司分区、分类覆盖和趋势引用。
- 程序计算覆盖率、虚构公开号、重复/错公司成员、缺失专利和非法 Evidence，不接受模型审计结论。
- 缺口按阶段生成 `FETCH/ANALYZE/CLASSIFY/PROFILE/TREND` 精确修复目标。
- 修复轮次未耗尽时路由 `REPAIR`，耗尽后诚实降级 `LIMITED`；虚构专利、集合逆序或 PRIMARY 分区损坏直接 `FAIL`。
- 空 U 可得到确定性满覆盖结果，避免除零或把“没有结果”误报为分析失败。

### 验证

- Coverage Audit、趋势与领域 Schema 聚焦测试 22 项通过。
- Landscape 全量 123 项测试、Python compileall 与 `git diff --check` 通过。
- Audit 为纯代码逻辑，无需真实模型 API Key。

## 2026-07-28 — landscape-evidence-bound-company-trends

### 已完成

- 新增跨公司趋势建议契约与分析服务，模型不再返回 Trend ID、精确日期、数量、比例或斜率。
- 程序从公开日计算 MONTH/QUARTER Bucket，并为验证后的趋势附加稳定 `TR-NN` 与 Time Basis。
- 每条趋势必须形成 company → publication → evidence 的完整有效链，引用公司必须恰好等于引用专利的归属公司。
- 增长、下降、出现、转向、加速和稳定等时间方向必须满足最少专利数与至少两个时间桶，否则 fail closed。
- 单家公司直接返回受限结果且不调用模型；公司 Profile 与 Analysis/日期集合不一致时拒绝生成趋势。

### 验证

- 跨公司趋势、Profile 和领域 Schema 聚焦测试 21 项通过。
- Landscape 全量 119 项测试、Python compileall 与 `git diff --check` 通过。
- 测试使用 Stub Model，未请求或消耗真实 API Key。

## 2026-07-28 — landscape-company-technology-profiles

### 已完成

- 新增公司技术 Profile 聚合服务，将程序验证过的 Categories 与模型叙述分离。
- 模型只返回整体总结、技术方向和限制；不接收或回显 company/category/publication/evidence ID、数量、日期或统计。
- 程序原样装配分类并再次验证成员覆盖，模型无法修改 Category ID、公开号、Evidence 或分类总结。
- 单件公司 Profile 直接从唯一分类确定性生成，不产生额外模型调用。
- Prompt 明确禁止在公司 Profile 阶段声称增长、下降、加速或转向，时间趋势留给后续程序统计门。

### 验证

- 公司 Profile、分类和领域 Schema 聚焦测试 20 项通过。
- Landscape 全量 115 项测试、Python compileall 与 `git diff --check` 通过。
- 测试继续使用 Stub Model，无需真实 API Key。

## 2026-07-28 — landscape-company-technology-classification

### 已完成

- 新增单公司技术分类结构化 Agent，只消费一个确定性 Company Batch 内的逐件分析，不接触其他公司上下文。
- 单件专利使用程序生成唯一分类，不调用模型；多件专利才调用结构化模型。
- 模型返回后由程序重新生成稳定 Category ID，并强制分类成员恰好覆盖本公司 `A`。
- 分类证据必须来自该分类成员专利，且每件成员专利至少贡献一个 Evidence；虚构公开号、跨专利证据和漏分均 fail closed。
- 新增独立 `CompanyTechnologyClassification` 契约，将分类输出与下一步公司 Profile 聚合解耦。

### 验证

- 公司分类、批次和领域 Schema 聚焦测试 20 项通过。
- Landscape 全量 112 项测试、Python compileall 与 `git diff --check` 通过。
- 测试使用 Stub Model，无需或消耗真实大模型 API Key。

## 2026-07-28 — landscape-deterministic-company-batches

### 已完成

- 新增纯确定性公司分析批次构建器，以持久化 PRIMARY 归属把成功分析集合 `A` 分桶。
- 公司按 `company_id`、批次内专利按公开号稳定排序，输入公司、Assignment 或 Analysis 的排列不会影响输出。
- Validator 强制 `A` 中每件专利恰好进入一个公司批次，拒绝重复、越界分析、未知公司引用、错桶和空批次。
- 抓取或模型失败对应的 `U-A` 不会伪造空公司任务；其缺口保留给覆盖审计。

### 验证

- 公司批次、归属与领域 Schema 聚焦测试 30 项通过。
- Landscape 全量 109 项测试、Python compileall 与 `git diff --check` 通过。
- 本提交只建立后续 LangGraph `Send` 的确定性输入，不提前引入动态并行。

## 2026-07-28 — landscape-complete-resumable-analysis

### 已完成

- `ANALYZE_PATENTS` 的目标集合改为全部成功抓取文档 `F`，彻底移除 `analysis_limit` 切片。
- 生产恢复路径直接从 PostgreSQL 重建完整 `FetchedDocument`，不再为恢复分析重新请求 Provider。
- 新增已持久化分析读取与内容哈希/公开号校验；重试时只调用尚未成功分析的文档，并合并既有结果。
- 若分析记录引用 `F` 之外的公开号则 fail closed，阶段输出显式给出目标数、已分析数、恢复数和完整性。

### 验证

- 分析恢复与 PostgreSQL 读取聚焦测试 16 项通过。
- Landscape 全量 106 项测试、Python compileall 与 `git diff --check` 通过。
- 单件模型失败仍作为明确覆盖缺口返回，后续由覆盖审计与有限修复策略处理。

## 2026-07-28 — landscape-resumable-complete-fetch

### 已完成

- `FETCH_DETAILS` 不再按 `analysis_limit` 抽样或补位，改为尝试抓取完整合格去重集合 `U`。
- 新增 `072_landscape_document_fetches`，持久化完整文档、内容哈希、成功/失败状态、错误和尝试次数，并以 canonical candidate 外键约束身份。
- 节点重试或进程重启时先加载已成功文档，只重试未成功专利；成功内容不可变，失败可累计尝试并升级为成功，迟到失败不能降级成功。
- 旧 `landscape_run_documents` 只记录成功文档元数据，避免失败占位元数据阻止后续成功恢复。

### 验证

- 详情抓取、Repository、Schema 与迁移聚焦测试 40 项通过。
- Landscape 全量 104 项测试、Python compileall 与 `git diff --check` 通过。
- 本提交尚未移除 `ANALYZE_PATENTS` 的旧 `analysis_limit`；全量精读在下一独立切片完成。

## 2026-07-28 — landscape-company-assignment-persistence

### 已完成

- 将确定性公司 Registry 与逐专利 PRIMARY 归属接入 `FILTER_AND_SELECT`，候选全集写入后立即使用原始 Run Scope 完成归属。
- PostgreSQL 采用 Run 行锁和单事务批量写入；首次写入后，同内容可幂等重放，内容变化、半套数据或旁路篡改均 fail closed。
- 新增 `071_landscape_company_assignment_manifest`，以数量和集合哈希冻结公司归属，包括公司和归属均为空的合法结果。
- 冻结数据库映射：用户竞争者、技术模式规范名称与 UNKNOWN 使用不同 `resolution_source`；confidence 是确定性解析标志，不作为概率。
- 现有领域契约无法把共同申请人无损映射为 company ID，因此发现非空 `co_assignees` 时明确拒绝持久化。

### 验证

- Landscape 全量 103 项测试通过；PostgreSQL Schema 与部署迁移 25 项测试通过。
- Python compileall 与 `git diff --check` 通过。
- 本提交未启动真实 PostgreSQL 容器；并发锁、事务回滚和真实迁移仍留在阶段集成门验证。

## 2026-07-28 — landscape-search-alias-trust-boundary

### 已完成

- 将用户确认别名与模型推断别名拆分为两种信任级别：前者可参与硬过滤和公司归属，后者只能扩展 Provider 查询。
- 搜索范围显式命名为 `search_scope`，保留用户别名并稳定去重合并模型推断别名。
- `FILTER_AND_SELECT`、公司统计、详情选择及后续公司归属统一读取不可变的原始 Run Scope。
- 增加端到端回归：模型别名可以进入查询，但仅命中模型别名的专利必须被 `COMPETITOR_NOT_CONFIRMED` 排除。

### 验证

- 规划、执行与检索聚焦测试 26 项通过；Landscape 全量 99 项通过。
- Python compileall 与 `git diff --check` 通过。
- 本提交不修改数据库或 Graph；公司归属持久化在下一独立切片实现。

## 2026-07-28 — landscape-deterministic-company-assignment

### 已完成

- 新增纯确定性公司归属服务与集合级 Validator，不调用模型、不写数据库。
- 竞争对手归属必须单独传入原始用户确认 Registry，不能直接消费带模型推断别名的 Effective Scope。
- 确认名称只做 NFKC、大小写和空白归一后的精确匹配；不做中文子串、英文词边界、法律后缀删除或相似度合并。
- 纯技术模式只合并精确规范化后相同的原始 Assignee，使用稳定 `CO-RAW-<hash>`；缺失或跨 Provider 冲突进入 UNKNOWN/REVIEW_REQUIRED。
- 集合 Validator 强制公司 ID/别名无冲突、Assignment 精确覆盖 `U`、无重复/越界并验证 matched alias 归属。
- 当前 Provider 契约只有单一 Assignee，因此不拆分或伪造共同申请人，`co_assignees` 保持空。

### 验证

- Landscape 全量 97 项测试通过。
- Python compileall 与 `git diff --check` 通过。
- 本提交尚未接入 Graph 或 PostgreSQL 公司表；接入前仍需拆分现有搜索中的用户确认别名和模型推断别名信任来源。

## 2026-07-28 — landscape-complete-eligible-set

### 已完成

- Landscape 去重改为只按规范化公开号分组；相同 Application 或 Family 的不同公开号不再被折叠。
- `strict_filter_and_select` 始终返回完整合格集合 `U`，排名只排序不抽样，`truncated_count` 固定为零。
- `candidate_limit` 改为 fail-closed 安全门：先将完整 `U` 写入 PostgreSQL，再以确定性错误终止，不生成被截断的伪全量报告。
- 生产 Runtime 显式注入 canonical candidate repository；SQLite 仅保留旧测试仓储职责。
- 候选哈希输入中的来源、Query 和排名理由稳定排序，保证步骤重试时幂等。
- 确定性安全门不再触发 LangGraph 重试；连接类瞬态错误仍可重试。

### 验证

- Landscape 全量 84 项测试通过。
- Python compileall 与 `git diff --check` 通过。
- 后续抓取/精读仍受旧 `analysis_limit` 影响，本提交只保证 `U` 完整，不声称已全量精读。

## 2026-07-28 — landscape-canonical-candidate-persistence

### 已完成

- 新增 PostgreSQL `070_landscape_company_analysis`，一次建立候选全集、公司归属、公司分类/Profile、跨公司趋势和洞察证据的持久化边界。
- 新增 canonical candidate 集合的 PostgreSQL 原生写入与读取：整组原子写入、规范公开号、连续排名、内容哈希、幂等重放和不可变冲突拒绝。
- 候选元数据写入前拒绝敏感字段；洞察证据使用 `(run_id, evidence_id)` 复合外键，禁止跨 Run 串证据。
- 将 `070` 接入已有数据卷的显式迁移命令，Landscape 启动门提升为必须存在 `070`。

### 验证

- Candidate Repository、PostgreSQL Schema、迁移命令、Adapter 和旧 Landscape Database 共 42 项测试通过。
- Python compileall 与 `git diff --check` 通过。
- 本切片未启动真实 PostgreSQL 容器；真实迁移留在阶段集成门执行。

## 2026-07-28 — landscape-company-trend-schemas

### 已完成

- 新增公司身份、逐专利公司归属、公司技术分类/Profile、跨公司趋势和覆盖审计的严格 Pydantic 契约。
- 模型输出不回显公司 ID、预期专利数和程序统计；这些事实由调用方持有，避免模型修改控制数据。
- 增加 UNKNOWN fail-closed、分类成员唯一、趋势引用唯一、时间窗口有序和 Audit 决策一致性校验。
- 趋势方向支持观察类和变化类标签，但时间桶充分性仍留给后续上下文 Validator 判断。

### 验证

- 公司趋势 Schema、Fixture、旧 Landscape Schema 和旧聚类共 30 项测试通过。
- Python compileall 与 `git diff --check` 通过。

## 2026-07-28 — landscape-company-trend-fixtures

### 已完成

- 新增 `BASE-01` 离线数据集，冻结 12 条原始命中、8 条合格命中、7 件按公开号去重专利及五个公司分组。
- 增加固定 Provider Hit、详情、公司别名与归属、逐件分析、公司 Profile、趋势、Graph 路径和 Expected 输出。
- 新增深层只读 Fixture Loader，拒绝目录逃逸、错误文件类型、空 JSONL 行和非对象记录。
- 新增 Fixture 契约测试，校验 `U/F/A/T`、公司分组、证据引用、公司 fan-out 和单时间桶禁止方向性趋势。

### 验证

- `PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_landscape_agent_fixtures -v`
- `git diff --check`

## 2026-07-28 — landscape-company-trend-agent-spec

### 范围

- 基于 `backend/landscape` 实际执行链，定义“全部去重合格专利”的集合口径、公司归属、公司内技术分类和跨公司趋势输出。
- 设计由固定直线工作流升级为公司级 `Send`、Reducer、覆盖审计、条件边和有限修复的目标 LangGraph。
- 定义 PostgreSQL 候选全集、公司归属、公司分析、趋势和证据关系的增量持久化方案。
- 提供 42 个以上模拟场景、确定性硬门槛、模型质量门槛、分阶段合并门和 22 个小粒度提交计划。

### 冻结边界

- 本次只生成待审查开发规格，不修改生产业务代码。
- 成功报告要求成功分析集合覆盖全部合格去重集合；存在失败时必须输出受限报告和准确覆盖率。
- 每个后续小功能必须在同一提交包含实现、测试、Fixture 和开发日志。

## 2026-07-28 — idea-high-recall-retrieval

### 运行故障复盘

- Run `720f6eee-5a72-4bfd-bd20-598c26a2be55` 并非卡在全文下载：6 条检索式中仅 1 条返回 20 条无关结果，4 条为空，1 条因 `IPC:(...)` 方言报错。
- 20 条命中的标题摘要相关性分数全部为 0，深读列表为空，因此没有发起任何全文请求；旧错误信息却将其描述为“没有可用全文”，并对同一确定性错误重试 3 次。
- SSE 断开后前端未回读持久化 Run，页面可能保留最后一个运行中步骤。

### 高召回改造

- Query Planner 明确采用 recall-first 策略：生成 8～16 条中英文检索式，每条最多两个概念组，IPC/CPC 只输出结构化候选，不直接生成 Provider 字段语法。
- 新增确定性 Query Strategy：将模型生成的三组以上 `AND` 检索式拆为一组或两组查询，补充中英文单概念和分类号兜底，单 Run 最多执行 24 条检索式。
- 新增 Provider 查询编译层：移除 `IPC:` / `CPC:` 等不兼容字段前缀；Exa 使用语义化纯文本，SerpAPI/Google Patents 使用受控 Boolean 子集。
- 候选池上限调整为 quick 60、standard 200、deep 400；全文深读上限仍为 10/20/40，避免候选召回量与高成本全文分析线性绑定。
- 保留标题摘要强相关优先级；当强相关候选不足深读下限时，按日期和标识有效性补入低置信候选进行全文核验，并记录 `LOW_CONFIDENCE_RECALL_BACKFILL`。补入候选不能仅凭摘要进入最终结论。
- 区分“没有可识别候选”与“候选全文/证据完整性全部失败”，不再用同一个“无全文”错误掩盖检索问题。
- `ExecutionGateError` 作为确定性业务门禁不再重试；连接错误等瞬态异常仍按原策略重试。
- SSE 断开后前端主动读取 PostgreSQL 中的最新 Run，避免终态已落盘但页面仍显示运行中。

### 验证

- 新增过度约束拆分、Provider 方言编译、零分候选补入全文核验、确定性门禁单次失败等模拟用例。
- 检索、筛选、Workflow、前端契约共 54 项通过；`node --check frontend/app.js`、Python compileall、diff check 通过。
- 全量 unittest 仍在既有同步 FastAPI `TestClient` 首次请求处等待，240 秒硬超时；线程栈停留在 AnyIO blocking portal，与本次检索代码无调用关系。

## 2026-07-28 — postgres-boolean-runtime-fix

### 根因与修复

- IDEA `NORMALIZE_AND_FETCH` 仍使用 SQLite 的 `deep_reviewed = 0/1` 和整数写入，PostgreSQL BOOLEAN 字段因此触发 `DatatypeMismatch` / `boolean = integer`。
- 将 retrieval、document analysis、execution、novelty、inventiveness、reporting、audit 的运行时 SQL 全部改为 `TRUE/FALSE`，INSERT 参数改为 Python `bool`。
- PostgreSQL compatibility adapter 增加遗留 predicate 和 INSERT 参数保护，避免漏网的 SQLite 风格布尔值再次进入 PostgreSQL。

### 验证

- 相关 repository/workflow/domain 回归 115 项通过，compileall 与 diff check 通过。
- 真实 PostgreSQL 临时记录完成 `FALSE → TRUE` 写入、查询和清理。
- 最新应用镜像已部署，App/PostgreSQL/MinIO 均 healthy；新容器日志未出现 `DatatypeMismatch`、`boolean = integer`、Traceback。

## 2026-07-27 — fresh-postgres-deployment

### 已完成

- 用户确认旧数据库数据无保留价值后，删除旧 PostgreSQL、对象存储、应用和遗留 Redis 数据卷，以空卷验证下一次部署。
- 仅替换构建中明显缓慢的下载源：Python 依赖使用腾讯云 PyPI，MinIO 的 Go 依赖使用腾讯云 Go 镜像；Python、PostgreSQL、Go、Debian 基础镜像仍保持原始固定版本。
- 从空数据卷执行 010～060 schema，构建并启动 App、PostgreSQL 和 MinIO。

### 验证

- App、PostgreSQL、MinIO 三个容器均为 `healthy`。
- `/api/system/health` 返回 `ok: true`，database/workflow_store 均报告 PostgreSQL ready。
- `idea_cases`、`idea_runs`、`landscape_runs` 均为 0，证明部署不依赖历史数据。
- 应用镜像中不存在 `/app/data/aifpatent` 和 `/app/data/langgraph`。

## 2026-07-27 — direct-postgres-cutover-implementation

### 已完成

- IDEA/Landscape runtime 改为从 `AIFPATENT_POSTGRES_DSN` 构建 PostgreSQL repository。
- 新增 `060_unified_runtime_schema.sql`：Landscape、cache metadata、workflow event/lease/idempotency。
- 新增 `tools/migrate_sqlite_to_postgres.py`，支持 dry-run、apply、verify、批量幂等导入。
- 固定 IDEA/Landscape/Follow-up 图不再创建 SQLite checkpoint。
- 移除 Redis requirements、Compose service、环境变量、适配器和对应测试。
- 删除 `httpx2` 依赖，并为 FastAPI/httpx 增加上限，避免未来版本漂移。
- 删除容器中 SQLite 数据目录和无用 app-data volume。

### 验证

- Python compileall 通过。
- PostgreSQL adapter/config/health/RAG/follow-up/execution/rag-infra 定向测试 57 项通过。
- 真实本机 PostgreSQL/MinIO 冒烟通过：统一仓储读取 5 个 IDEA cases、55 张表；临时 Landscape 联合模式完成创建、JSONB stage 写入、状态迁移和删除。
- SQLite → PostgreSQL `--apply` 已完成且可重复执行；`--verify` 返回 `ok: true`，21 张 IDEA 来源表的主键缺失数为 0。目标库中原有额外数据被保留并在报告中单独计数，不做静默删除。
- `--dry-run` 现支持显式参数；`pip check` 和 `compileall` 通过。
- 全量测试在同步 FastAPI endpoint 上仍受当前沙箱线程池限制而挂起；最小 async endpoint 可运行。该限制不影响定向测试和生产容器路径，仍需在 CI/真实环境完成全量回归。

### 运行时边界

- SQLite 文件和 SQLite 导出/回填脚本仅作为离线迁移输入与备份保留，不进入应用镜像、启动路径或业务写入路径。
- Landscape PostgreSQL adapter 复用少量领域方法以保持 API contract；连接、事务和表访问全部由 PostgreSQL adapter 提供，运行时不会打开 SQLite 文件。

## 2026-07-27 — migration-decision-approved

### 已确认

- 本独立分支直接迁移到 PostgreSQL，不做长期双写、shadow-read 或线上维护窗口。
- SQLite 仅保留为一次性迁移输入、校验来源和离线备份；迁移验证完成后移除运行依赖。
- Redis 当前没有进入运行时装配，本次从依赖、Compose、健康检查和未使用适配器中移除。
- 未来只有出现持久队列、跨进程 lease 或分布式限流需求时才重新引入 Redis。

## 2026-07-27 — unification-migration-proposal

### 范围

- 新增统一 PostgreSQL、SQLite 一次性迁移、代码瘦身、依赖收敛和健壮性增强方案。
- 当前仅为待审查提案，未执行数据库迁移或生产源码删除。

### 待确认决策

- PostgreSQL 是否成为所有业务状态的唯一事实源；
- 是否接受维护窗口/短暂只读而非长期双写；
- Redis 是正式接入还是移除；
- 是否逐步移除 SQLite LangGraph checkpoint；
- 迁移完成后是否删除 SQLite runtime。

## 2026-07-27 — baseline-v1

### 范围

- 重新遍历实际源码、运行装配、存储、工作流、测试和部署配置。
- 建立 Agent 升级开发入口、实态审计、升级规格、测试方案和 12 条合成决策用例。

### 关键决策

- 项目定位为“证据约束的专利研究 Agent”。
- 首版采用单 Supervisor + 确定性工具，不直接采用多 Agent。
- 新 Agent 状态以 PostgreSQL 为唯一事实源。
- 自主行为限定在检索/补证决策循环，权限、预算、引用和状态由代码控制。
- 外部新研究使用 proposal + approval + child run。

### 验证

- `python3 -m compileall -q backend tools`：通过。
- 全量 unittest：未闭合。前 13 项通过，API TestClient 用例处 45 秒超时。

### 未完成

- 尚未修改生产代码。
- 尚未实现评测运行器。
- 尚未定位 API 测试挂起根因。
- 合成集尚未经过模型实跑；当前仅作为设计 oracle。

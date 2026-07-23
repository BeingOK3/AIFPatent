# 专利态势分析开发日志

本文件只追加，不覆盖历史记录。每条记录包含日期、工作单元、涉及文件、验证和未完成边界。

## 2026-07-22 — LANDSCAPE-DESIGN-001

- 类型：独立专利态势分析 MVP 技术基线。
- 决策：采用 `TECHNOLOGY`/`COMPETITOR` 两种显式模式；“新公开”按包含式公开日窗口硬过滤，申请日只用于筛选后趋势；友商别名由用户确认，模型不能扩大严格过滤集合。
- 架构：新建 `backend/landscape/`、独立 SQLite/Checkpoint/报告目录和 `/landscape` 页面；现有 IDEA 11 步、追问、Schema、Prompt 和前端状态机冻结，只在共享 FastAPI 入口做最小装配。
- 非 RAG 边界：精读直接使用摘要、权利要求和说明书证据包；MVP 不使用 Chunk Retriever、Embedding、pgvector、RRF 或 Context Manifest。
- 质量门：统计由程序计算；日期缺失不进入主结果；当前权利人和同族来源不足时显示未知/不完整；模型不得生成元数据、越界公开号或伪证据；API Key 不持久化。
- 涉及文件：`development/landscape/README.md`、`architecture.md`、`implementation-plan.md` 和本日志。
- 验证：待执行文档 diff 检查并提交；本工作单元不修改运行时代码。

## 2026-07-22 — LANDSCAPE-DOMAIN-002

- 类型：领域契约、独立持久化和报告存储。
- 实现：新增严格 Pydantic 输入/输出 Schema，覆盖两种分析模式、公开日窗口、友商确认别名、运行预算、直接证据引用、逐件精读和聚类契约。
- 持久化：新增 `LandscapeDatabase` 和独立 `landscape_*` 表；Run 输入与配置受 SQLite Trigger 保护，步骤结果按内容哈希幂等且不可覆盖，运行状态按有限状态转换。
- 文件：新增 `LandscapeRunStore`，原子写入 `input.json`、`report.json`、`report.md`、`patents.csv`，最后生成带大小与 SHA-256 的 `manifest.json`。
- 安全：数据库配置、阶段结果、报告和 Manifest 元数据写入前递归拒绝 API Key、Token、Authorization、Password、Secret 等敏感字段；重启后遗留活动 Run 以明确错误码失败。
- 冻结确认：未修改 `backend/idea/`、IDEA 数据表、Prompt、Workflow 或前端状态机。
- 涉及文件：`backend/landscape/{__init__,schemas,database,store}.py`、三个 `test_landscape_*` 测试文件及本日志。
- 验证：新增 14 个单元测试通过；全量 `backend/tests` 回归退出码为 0，`git diff --check` 通过；待提交并按两提交节奏推送。

## 2026-07-22 — LANDSCAPE-SEARCH-003

- 类型：查询规划与严格范围检索。
- 实现：新增无需模型即可工作的确定性查询规划，并校验每个 Plan 必须保留用户技术方向或已确认友商锚点；查询数量固定在 2～6 条。
- 范围：每条 Provider Hit 在合并前执行公开号、公开日和友商过滤；公开日窗口两端包含，缺失/非法/越界日期均排除；友商英文名称按词边界匹配，中文别名按标准化文本匹配，避免 `Meta` 误命中 `Metallurgy`。
- 覆盖：复用现有 Provider 契约、Runner 和确定性合并器；持久返回原始命中、合格命中、唯一候选、选中、截断及逐原因排除计数，Provider 状态按查询保留。
- 预算：去重后严格应用 `candidate_limit`，超限数量显式记录，不把被截断集合描述为完整结果。
- 冻结确认：只读取现有 `idea.providers`/`idea.merge` 公共抽象，未修改任何 IDEA 文件。
- 涉及文件：`backend/landscape/{planning,search}.py`、`test_landscape_{planning,search}.py` 及本日志。
- 验证：Fixture 单元测试覆盖窗口边界、日期缺失/非法/越界、无效公开号、友商误匹配、跨查询去重和预算截断；待提交。

## 2026-07-22 — LANDSCAPE-ANALYSIS-004

- 类型：非 RAG 证据包、逐件专利精读和结构化技术聚类。
- 证据：从当前 `FetchedDocument` 的摘要、权利要求、背景技术和方向术语命中说明书片段直接构建受字符预算保护的证据包；每段记录来源类型、标签、原文 offset 和 SHA-256，不调用 Chunk Retriever、Embedding 或向量库。
- 精读：新增独立 Landscape 模型输出契约与 Prompt，要求现有技术、问题、核心发明点、解决问题和有益效果均绑定当前文献证据；未知证据 ID、公开号串件和缺少支持的结论 fail closed。
- 容错：多文献精读使用有界并发，单件失败按公开号记录，不取消其他已完成精读，供工作流最终标注限制。
- 聚类：只向模型提供标题、摘要、核心发明点和关键词；成员必须恰好覆盖成功精读集合，重复、遗漏、新增公开号和重复簇 ID 全部拒绝；单文献使用稳定确定性簇。
- 持久化：补充文献元数据、证据、分析和聚类不可变写入；证据原文留存但不在 SQLite 保存整篇专利全文。
- 冻结确认：复用 `StructuredModelClient` 和 Agent Schema 注册扩展点，未修改 `backend/idea/`，未引入 RAG。
- 涉及文件：`backend/landscape/{analysis,clustering,database}.py`、对应单元测试及本日志。
- 验证：证据 offset/hash/类型、未知证据、公开号越界、缺少证据支持、单件簇及重复/遗漏/未知聚类成员测试通过；待全量 Landscape 回归并提交。

## 2026-07-22 — LANDSCAPE-RUNTIME-005

- 类型：固定 LangGraph 工作流、运行时、API 与报告。
- 工作流：新增 8 个固定节点 `VALIDATE_SCOPE -> PLAN_SEARCH -> SEARCH_PUBLICATIONS -> FILTER_AND_SELECT -> FETCH_DETAILS -> ANALYZE_PATENTS -> CLUSTER_PATENTS -> BUILD_REPORT`，每个节点受步骤顺序、尝试次数、SQLite 状态和独立 checkpoint 保护。
- 运行时：新增独立 `landscape.db`、`landscape-checkpoints.db`、`workspace/landscape-runs/` 装配；Provider 仅复用公共接口且不写 IDEA Cache/数据库；BYOK 只存在进程内 TaskManager，重启活动 Run 以明确错误码失败。
- API：新增 Run 创建、历史、SSE 进度、取消、重跑、JSON/Markdown/CSV 报告和删除接口；共享入口仅在 `main.py` 装配新 Router 与 `/landscape` 路由，没有改 IDEA Router/Workflow/前端状态机。
- 报告：程序计算申请日趋势、公开法域、逐件元数据和限制；模型只负责逐件解释与聚类，`manifest.json` 最后原子写入并回写数据库哈希记录。
- 安全：API Key 使用 `SecretStr`，配置快照只保存模型、无凭证的 base URL 和内存凭证来源标记；报告下载前始终校验 Manifest。
- 涉及文件：`backend/landscape/{workflow,execution,runtime,api,reporting}.py`、`backend/main.py`、工作流测试及本日志。
- 验证：Landscape 领域、检索、分析、聚类和工作流共 26 个测试通过，待全量回归、页面实现后提交。

## 2026-07-22 — LANDSCAPE-UI-006

- 类型：独立专利态势分析页面。
- 页面：新增 `/landscape`、`landscape.html`、`landscape.css`、`landscape.js`；提供技术方向/友商模式、公开日预设与自定义窗口、候选/精读预算、历史 Run、步骤进度、取消、趋势/法域条形统计、技术聚类、逐件精读、限制和 Markdown/CSV 下载。
- 安全：前端所有动态文本经过 HTML 转义；API Key 使用密码输入框，不写 `localStorage`、Cookie 或 URL；现有 `frontend/app.js`、IDEA 页面和状态机未改动。
- 可用性：页面直接使用 Landscape API/SSE，移动端响应式布局；模式切换自动更新必填字段，时间预设自动填充公开日窗口。
- 涉及文件：`frontend/landscape.html`、`landscape.css`、`landscape.js`、本日志。
- 验证：待执行 Node 语法检查、前端契约检查、全量回归和提交后推送。

## 2026-07-22 — LANDSCAPE-ACCEPTANCE-007

- 类型：容器、入口和真实模型验收。
- 容器：应用镜像已重建，PostgreSQL、Redis、MinIO、App 四服务健康；OpenAPI 暴露 11 条 Landscape API 路径，`/landscape` 页面响应正常；小预算创建/取消契约测试通过。
- 真实 Run：使用临时 DeepSeek BYOK、候选上限 10、精读上限 1 完整走完 8 个节点，状态为 `COMPLETED_WITH_LIMITATIONS`；Exa/Google 搜索命中公开日不足，严格日期门排除 10 条，Google Provider 同时受限流影响，报告明确记录限制，未伪造专利精读。
- 修正：增加缺失公开日的受控详情元数据补全；只有补全得到真实 ISO 公开日才重新进入严格窗口过滤，补全失败仍排除；成功补全的全文复用于后续详情阶段，避免重复抓取。
- 修正：由程序在每条 Provider Query 后追加公开日起止提示，并继续在返回后执行包含式硬过滤；查询提示扩大一日以适配 Provider 的严格 `after/before` 语义，最终范围仍以用户窗口为准。
- 缺陷修复：真实 Run 已获得窗口内候选并成功调用 DeepSeek 精读，但聚类仓储与工作流重复写入同名阶段结果，触发不可变冲突；调整为阶段结果仅由 Workflow Harness 持有，聚类业务表通过自身内容重建进行幂等校验。
- 凭证：真实 Run API Key 未写入代码、Git、Landscape SQLite、报告或日志；当前环境变量没有默认模型凭证。
- 验证：Node 语法检查、Landscape 26 个测试和容器健康检查通过；补全逻辑测试与全量回归待执行。

## 2026-07-22 — LANDSCAPE-ACCEPTANCE-007-COMPLETE

- 真实结果：修复后使用失败 Run 的不可变输入重跑成功，8/8 节点完成；严格窗口内唯一候选 5 件，按预算成功精读 1 件 `US20260082513A1`，生成 1 个技术簇。
- 报告：`report.json`、`report.md`、`patents.csv`、`input.json`、`manifest.json` 全部存在，Markdown/CSV 下载 HTTP 200；Manifest 完成门通过。
- 限制：Google Patents Provider 当前不可用，Exa 主链路正常；部分详情补全专利位于窗口外并被硬过滤，报告记录 `PUBLICATION_DATE_OUTSIDE_WINDOW` 和 `PROVIDER_FAILURE`。
- 凭证核验：Landscape 配置快照仅包含 `base_url`、`credential_source`、`model`，凭证来源为 `per_run_memory`；仓库敏感 Key 模式扫描无命中。
- 回归：Landscape 29 个单元测试通过；既有前端/容器/启动脚本 12 个针对性回归通过；Node 语法和 `git diff --check` 通过；四个 Docker 服务保持健康。
- 结论：MVP 可从 `/landscape` 创建任务并产出一件以上真实新公开专利精读、统计、聚类和可下载报告；IDEA 业务代码和页面状态机未修改。

## 2026-07-22 — LANDSCAPE-DOCS-008

- 类型：MVP 验收归档。
- 实现：新增 `acceptance.md`，固化页面/API 入口、真实 Run 输入与输出、测试/容器/凭证核验、已知 Provider 限制和下一批建议；实施计划标记完成。
- Git 节奏：本提交作为第 8 个工作单元，将与第 7 个真实验收修复提交一起推送远程 `develop`。
- 涉及文件：`development/landscape/{README,implementation-plan,acceptance,development-log}.md`。
- 验证：文档不包含 API Key；`git diff --check` 待提交前执行。

## 2026-07-22 — LANDSCAPE-ENTRY-009

- 类型：首页业务入口补充。
- 原因：独立页面和 `/landscape` 路由已可用，但 IDEA 首页没有可见导航，用户只能手工输入 URL。
- 实现：首页右上方新增“专利态势分析 / 生成新公开专利调查报告”入口卡片，桌面端与系统状态并列，移动端自适应整行展示。
- 冻结边界：仅修改 `frontend/index.html` 和 `frontend/style.css` 的静态导航，未修改 `frontend/app.js` 或 IDEA 业务状态机。
- 测试：`test_frontend.py` 新增入口 URL、名称、报告说明和样式契约；前端 7 个测试通过，`git diff --check` 通过。
- Git：功能提交 `a053de6`；本日志提交后将按两提交节奏一起推送远程。

## 2026-07-22 — LANDSCAPE-MODES-DESIGN-011

- 类型：三模式、友商别名和运行调试增量设计。
- 决策：页面不显示模式单选框；根据技术方向和友商是否填写自动派生 `TECHNOLOGY`、`COMPETITOR`、`TECHNOLOGY_COMPETITOR`，后端复算并显式持久化，兼顾输入简洁和运行可审计。
- 别名：用户只输入友商主名称；模型在 `PLAN_SEARCH` 逐一生成最多 12 个检索别名，程序禁止新增主体并规范化去重；失败降级为主名称，实际使用别名进入报告和调试。
- 调试：新增独立 `/api/landscape/runs/{run_id}/debug`，页面展示步骤尝试、查询、Provider 状态、命中/排除计数和错误，不返回凭证或完整模型请求。
- 展示：首页入口删除“新业务模块”字样，仅保留“专利态势分析”及报告说明。
- 涉及文件：`architecture.md` 和本日志；运行时代码待下一工作单元实施。

## 2026-07-22 — LANDSCAPE-MODES-BACKEND-012

- 类型：三模式自动判定、友商别名解析与检索范围执行。
- 模式：`LandscapeScope` 根据技术方向和友商输入自动派生 `TECHNOLOGY`、`COMPETITOR` 或 `TECHNOLOGY_COMPETITOR`；客户端若显式传入不匹配模式则拒绝，组合模式同时保留技术方向和友商两个检索锚点。
- 别名：`PLAN_SEARCH` 使用结构化模型逐一生成友商专利申请人别名，禁止改变用户指定主体、跨友商名称碰撞或新增主体；模型失败时降级为主名称并生成明确限制项。
- 执行：查询生成、Provider 检索和严格申请人过滤均使用本次解析后的有效别名；实际检索别名随阶段结果进入最终报告，原始 Scope 仍保持不可变。
- 兼容：新库允许组合模式；旧版 SQLite 的模式约束无需破坏性迁移，组合模式以不可变 `scope_json` 为权威值并保持读取结果一致。
- 验证：Landscape 模式、规划、数据库、检索等 36 个测试通过；覆盖三种自动派生、显式模式冲突、别名主体防护、失败回退、组合模式双锚点和申请人硬过滤。
- Git：本功能将作为第 12 个工作单元提交，并与第 11 个设计提交一起推送远程 `develop`。

## 2026-07-22 — LANDSCAPE-DEBUG-013

- 类型：专利态势分析运行调试 API。
- 接口：新增 `GET /api/landscape/runs/{run_id}/debug`，返回步骤尝试及耗时、已持久化检索式、友商别名解析结果、Provider 状态、候选覆盖和命中/排除聚合统计。
- 脱敏：调试视图不读取或返回 `raw_json`、完整模型输出、请求头、API Key 或运行配置快照；错误信息限制长度，查询内容仅保留业务检索式。
- 兼容：未改变 IDEA 调试接口和 Landscape 工作流状态机；调试数据来自 Landscape 自有 SQLite 只读聚合。
- 验证：数据库调试快照覆盖步骤耗时、查询、聚合计数和原始 Provider Payload 排除；相关测试通过。

## 2026-07-22 — LANDSCAPE-UI-MODES-014

- 类型：专利态势分析页面自动模式、别名和运行调试展示。
- 输入：移除模式单选框，技术方向和重点友商均可选填；页面根据已填写内容即时显示三种派生模式，提交时至少要求一类输入，后端仍负责最终校验。
- 友商：每行只填写一个主名称；页面不要求用户维护别名，报告和运行调试分别展示模型推断的别名、来源以及回退提示。
- 调试：Run 面板新增“系统运行调试”，实时/终态展示步骤尝试和耗时、检索式、Provider 状态、覆盖计数、排除统计和别名解析错误。
- 首页：入口保留“专利态势分析”和报告说明，删除“新业务模块”标签。
- 安全：动态展示文本继续经过 HTML 转义；调试接口仅显示后端聚合结果，不在前端保存 API Key。
- 验证：Node JavaScript 语法检查、前端契约测试和 Landscape 后端相关测试通过；待容器重建后进行页面/API联调。

## 2026-07-22 — LANDSCAPE-COVERAGE-DESIGN-015

- 类型：最近 Run 故障复盘与完整检索覆盖设计。
- 事实：联合 Run `dcd661c7` 的“数据中心液冷”只生成中文技术词；4 家友商因 6 条全局上限仅实际查询第一家中科曙光。Exa 返回 252 条但被公开日/申请人硬过滤为 0，缺失公开日补全耗时约 552 秒；随后友商 Run 的 Exa 请求全部 HTTP 429，Google Patents 从容器直连超时。
- 覆盖门：技术方向必须保留原文并生成英文专利检索词；每个输入友商必须至少有一条独立查询；联合模式逐友商覆盖原始/中文与英文方向组，程序校验任何漏检并 fail closed。
- 执行策略：查询上限改为按友商动态分配的 40 条硬上限；同一友商别名合并为有界 OR 组；Provider 使用独立查询提示、有界并发和 Run 内熔断；缺失日期详情补全先去重并受候选预算约束。
- 验收：测试至少覆盖 4 家友商全部出现在查询计划、中文方向产生英文查询、联合模式逐友商双语覆盖、Exa 串行和 Google 超时熔断、重复公开号只补全一次。
- Git：本设计作为第 15 个工作单元，核心实现作为第 16 个工作单元，完成后一起推送远程 `develop`。

## 2026-07-22 — LANDSCAPE-COVERAGE-CORE-016

- 类型：双语技术扩展、逐友商完整覆盖、Provider 调度和日期补全去重。
- 双语：新增结构化技术方向扩展 Agent，必须原样保留用户方向并同时返回中文专利术语和英文专利术语；任一语言缺失或模型改写原始方向时 fail closed，不再以单语结果冒充完整检索。
- 逐友商：仅友商模式每家生成名称组和申请人字段组两条查询；联合模式每家生成原始/中文方向组与英文方向组两条查询。计划最多 40 条，校验器逐一验证每个友商及联合方向锚点。
- Provider：Google 与 Exa 使用各自日期提示；同一 Provider 查询串行执行，Google 首次超时后对当前 Run 快速熔断，Exa 请求至少间隔 1 秒并在 429 后增加冷却。
- 补全：缺失公开日命中先按 `provider + publication_number` 去重，尝试数量受候选预算限制；同一专利跨查询重复命中复用一次详情结果，并持久化补全统计。
- 验证：44 个 Landscape 测试通过；新增 4 家友商逐家双语覆盖、仅友商逐家覆盖、Provider 串行、Google 超时熔断、Exa 专用日期提示和重复公开号单次补全断言。
- Git：本实现作为第 16 个工作单元，与第 15 个设计提交一起推送远程 `develop`。

## 2026-07-22 — LANDSCAPE-COVERAGE-DEBUG-017

- 类型：完整检索覆盖的可视化审计与真实 Provider 错误展示。
- Debug：新增中英文技术扩展词、每次 Provider 调用的 query/provider/status/耗时/命中数/错误码/错误信息，以及缺日期命中、去重后公开号、实际补全、复用和截断统计。
- 别名审计：分别输出模型识别别名、实际进入查询的 `searched_aliases` 与受查询长度限制未采用的 `unsearched_aliases`；最终报告只把实际采用部分描述为检索别名。
- 安全：Provider 调试只读取状态和最多 500 字符错误信息，不返回命中列表、`raw_json`、请求头、模型请求或凭证。
- 验证：Node 语法、Debug 脱敏/聚合测试、前端契约和全部 44 个 Landscape 测试通过。

## 2026-07-22 — LANDSCAPE-COVERAGE-ACCEPTANCE-018

- 类型：完整检索覆盖容器验收。
- 镜像：应用镜像按最新代码重建，App、PostgreSQL、Redis、MinIO 四服务均为 healthy；`/landscape` 与 Debug API 可访问。
- 覆盖实测：容器内使用“数据中心液冷”及中科曙光、华为、英伟达、浪潮四家友商构造计划，得到 8 条查询；每家分别包含一条原始/中文技术词组查询和一条英文技术词组查询，计划校验通过。
- 查询示例：华为覆盖 `("数据中心液冷" OR "冷板液冷") AND ("华为" OR "Huawei")` 与 `("data center liquid cooling" OR "cold plate cooling") AND ("华为" OR "Huawei")`；其余三家保持相同双语覆盖结构。
- Debug 实测：旧 Run 可返回 12 条 Provider 调用明细及 `PROVIDER_TIMEOUT` 等真实错误；旧阶段结果不回填技术扩展，新建 Run 才使用并展示双语扩展，保持不可变运行语义。
- 回归：44 个 Landscape 测试、13 个既有前端/容器/启动脚本测试和 Node 语法检查通过，`git diff --check` 通过。
- Git：本验收为第 18 个工作单元，将与第 17 个 Debug 提交一起推送远程 `develop`。

## 2026-07-22 — LANDSCAPE-SERPAPI-DESIGN-019

- 类型：SerpAPI Google Patents 第三 Provider 增量设计。
- 主链路：新增 `SerpApiPatentProvider`，使用 `google_patents` 做结构化专利检索、`google_patents_details` 获取摘要/权利要求/同族基础信息，继续复用统一 Provider 契约、严格公开日与申请人过滤、跨源去重和 Debug。
- 凭证：根据部署约定改为服务器本地 `config/provider-credentials.local.json`；Git 和 Docker build 均忽略真实文件，仓库只提交 JSON 模板，Compose 以只读 Secret 注入。页面和 Run API 不接收 SerpAPI Key，SQLite、Run 文件、Debug 和错误信息均不保存值。
- 三路分工：SerpAPI 定位为结构化主召回与首选详情源，Exa 定位为自然语言语义补召回，Google 直连定位为网络健康时的低成本补充。首版完整 fan-out 便于比较覆盖，后续推荐默认 `BALANCED` 调度。
- 配额：SerpAPI 精确请求缓存应开启，查询使用官方日期参数并关闭 Scholar；详情只对严格过滤后的精读集合或日期缺失候选执行，避免按所有原始命中消耗额度。
- Git：本设计为第 19 个工作单元；实现和验收作为第 20 个工作单元，两个提交后推送远程 `develop`。

## 2026-07-22 — LANDSCAPE-SERPAPI-CORE-020

- 类型：SerpAPI Google Patents Provider、本地 JSON 凭证和容器验收。
- Provider：新增 `serpapi_google_patents` 搜索与详情实现，结构化映射公开号、日期、权利人、摘要、权利要求、Family 和全球申请信息；统一参与三 Provider fan-out、严格过滤、跨源去重、日期补全与详情回退。
- 配额与诊断：确定性请求复用现有 Cache，缓存键和内容均不含 Key；401/403/429、响应错误及网络异常转换为稳定错误码，凭证/鉴权/额度错误后对当前 Run 熔断，其余 Provider 继续运行。
- 凭证：真实 Key 只存在 `config/provider-credentials.local.json`，权限强制为 `0600`；Git 与 Docker build 均忽略该文件，只跟踪 `provider-credentials.example.json`。前端、Run 请求、配置快照和 Debug 不接收或输出 Key。
- 容器：Compose 将宿主机私密文件只读挂载，入口复制为 UID/GID 10001、权限 `0400` 的运行时 Secret 后立即降权；容器 PID 1 为 UID/GID 10001，镜像 `/app/config` 不包含真实文件，四项服务均 healthy。
- 验证：SerpAPI Provider、配置、Landscape 调度、前端、基础设施和容器契约共 47 个定向测试通过；Python 编译、Node 语法、JSON 解析与 `git diff --check` 通过。真实 Key 扫描确认已跟踪文件命中数为 0。
- 限制：外部 SerpAPI 受控请求因当前工具网络授权限制未执行；已完成可注入 Transport 的搜索/详情/错误/缓存契约测试，部署环境真实检索保留为下一次联调项。
- Git：本实现作为第 20 个工作单元，与第 19 个设计提交一起推送远程 `develop`。

## 2026-07-22 — LANDSCAPE-PROVIDER-INCIDENT-021

- 类型：最新 Run 的网络、Provider 和凭证日志事故复盘。
- 结论：Docker DNS 和一般 HTTPS 出站正常；Google Patents 单站直连超时，Exa 为 HTTP 429，SerpAPI 为查询语义/空结果分类错误，不是三个搜索引擎都无法联网。
- 安全：发现 `httpx` INFO 把带查询参数的 SerpAPI URL 写入旧容器日志；修复要求关闭底层完整 URL 日志、重建容器并建议轮换已暴露 Key。
- 决策：当前部署默认只启用已实测连通的 SerpAPI；保留 Exa/Google Provider，通过配置在具备额度或网络条件的环境重新启用。
- 设计：详见 `network-provider-incident.md`；代码、测试与容器验收作为第 22 个工作单元。

## 2026-07-22 — LANDSCAPE-PROVIDER-RECOVERY-022

- 类型：默认搜索链路恢复、限流熔断、日期规范化和日志凭证修复。
- SerpAPI：纯申请人 OR 检索转换为官方 `assignee` 参数，名称含逗号时使用括号；“未返回结果”映射为成功空集，不再误报 Provider 故障。真实结构化申请人检索返回 10 条结果。
- 调度：当前环境默认只启用 SerpAPI；匿名 Exa 和 Google 直连默认关闭但保留实现。Exa 重新启用后若首次 429，会立即对当前 Run 熔断；Google/Exa/健康探测均补齐代理连接超时后的直连回退。
- 健康：新增 SerpAPI 本地凭证健康项；disabled Provider 被视为正常配置且不计入在线可用性，部署结果为 `overall=ok`、SerpAPI=`configured`。
- 日期：修复最新季度 Run 实际只有一个月的问题；非自定义预设由后端按结束日强制规范为 1/3/6/12 个自然月，前端同步使用 UTC 和月末安全计算。
- 安全：关闭 `httpx`/`httpcore` 完整 URL INFO 日志；启动时定向脱敏旧日志 query-string Key。6 个历史值已替换为 `[REDACTED]`，持久化日志和新容器 stdout 的真实 Key 命中均为 0。
- 验证：49 项 Landscape 测试、74 项 Provider/健康/配置/容器测试、Python 编译、Node 语法和 Diff 检查通过；App、PostgreSQL、Redis、MinIO 均 healthy。
- Git：本实现作为第 22 个工作单元，将与第 21 个事故分析提交一起推送远程 `develop`。

## 2026-07-23 — LANDSCAPE-SELECTION-REPORT-DESIGN-023

- 类型：大结果集选样、公司分布、聚类成员和全族状态 V2 设计。
- 统计口径：公司柱状图使用公开日/模式硬过滤并跨查询去重后的全部唯一合格专利；原始命中存在重复，候选集受预算截断，二者均不作为公司数量口径。
- 选样：候选以 RRF、Query/Provider 覆盖和技术文本匹配生成可审计分；友商模式采用 40% 均衡基线与 60% 实际数量比例的混合配额，精读再保证公司覆盖、同族代表和失败补位。
- 报告：取消申请日趋势图；聚类成员补充确认友商/当前权利人和申请日；精读新增结构化全族成员、法域和法律状态汇总。
- 边界：聚类继续明确限定为成功精读集合；全族来源不足只输出 `PARTIAL/UNAVAILABLE`，不宣称全球完整；历史 Run 不回填。
- 文档：详见 `selection-report-v2-design.md`；实现、回归和容器验收作为第 24 个工作单元。

## 2026-07-23 — LANDSCAPE-SELECTION-REPORT-CORE-024

- 类型：大结果集可解释选样、公司分布、聚类成员与全族状态 V2 实现。
- 全集统计：严格公开日/友商过滤后先跨检索式和 Provider 去重，再按确认友商主名称或规范化当前权利人统计全部唯一合格专利；报告和页面不再用原始重复命中或受预算截断的候选集计算公司数量。
- 选样：候选按 RRF、检索式覆盖、Provider 覆盖和技术文本匹配评分；友商模式应用均衡/比例配额，精读按公司轮转，并在详情抓取失败时继续从候选队列递补，直到达到精读目标或候选耗尽。
- 调试：Debug 新增最多 200 条无正文候选排序审计，页面展示前 30 条的综合分、入选结果和确定性原因；公司统计口径随 Coverage 返回。
- 报告：Schema 升级为 `landscape-report/1.1.0`；删除申请日趋势，增加唯一合格专利公司柱状图；公开法域改为辅助统计；技术聚类成员增加确认友商/当前权利人和申请日。
- 全族：逐件精读增加 Family ID、数据可用性、总体法律状态、法域、结构化同族成员、成员申请日/状态和当前申请标记；只把 Provider 返回的结构化成员标记为 `PARTIAL`，无成员时为 `UNAVAILABLE`，不宣称全球完整。
- 下载：Markdown 与 JSON 使用相同统计和成员口径；CSV 增加全族总体状态、法域和成员摘要列。
- 验证：53 项 Landscape 测试、45 项 SerpAPI/配置/容器契约/启动脚本/前端/健康/日志安全测试、Python 编译、Node 语法和 `git diff --check` 通过；完整仓库发现式测试停在既有 `test_api.IdeaApiTests.test_attachment_names_are_scoped_to_upload_directory`，未作为本功能通过结论。
- 容器：最新代码镜像重建成功，App、PostgreSQL、Redis、MinIO 均 healthy；`/api/health` 返回 `ok`，`/landscape` 和新版静态资源可访问。
- Git：本实现作为第 24 个工作单元，与第 23 个设计提交组成一对，提交后推送远程 `develop`。

## 2026-07-23 — LANDSCAPE-QUERY-SELECTION-DESIGN-025

- 类型：中英文合并检索式与精读加权 V3 设计。
- 查询：技术方向只生成一条中英文 `OR` 查询；友商/联合模式每家公司生成一条完整查询，四家公司由 8 条降为 4 条，同时避免全局 Top-N 让单一公司挤占其他公司召回。
- 去重：合并查询只减少跨语言重复，不替代公开号、申请号和确认 Family ID 的确定性去重。
- 精读：名额足够时每家公司先保留一件，剩余名额按唯一合格专利数量使用 D'Hondt 方法分配；公司内部优先结构化同族法域覆盖更广的专利。
- 证据边界：精读前只使用 Provider `country_status` 计算 `family_footprint`，不把它冒充完整同族成员数；精读后全族状态仍来自结构化详情。
- 文档：详见 `query-selection-v3-design.md`；实现、测试和运行验收作为第 26 个工作单元。

## 2026-07-23 — LANDSCAPE-QUERY-SELECTION-CORE-026

- 类型：中英文合并检索式、公司数量加权和同族覆盖优先实现。
- 查询规划：`LandscapeQueryPlan` 最少允许一条查询；技术方向模式生成一条 `mixed` 中英文 `OR` 查询，友商/联合模式每家公司生成一条查询。两友商本地冒烟得到 2 条混合查询，并逐条包含中文方向、英文扩展词和对应友商。
- 去重：原 `merge_hits` 公开号、申请号和确认 Family ID 去重保持不变；合并检索式只减少跨语言重复调用，不把它描述为绝对无重复。
- 精读：新 `weighted_analysis_selection` 在预算足够时先为每个规范化公司分配一件，再按唯一合格专利数使用确定性 D'Hondt 权重分配剩余名额；同公司队列按 `family_footprint`、原候选相关性顺序和公开号排序。
- 同族口径：`family_footprint` 只统计检索结果 `country_status` 中不同法域，Family ID 无法域明细时最低记 1；该值进入候选 Debug 和前端，不替代详情阶段的结构化全族成员。
- 补位与调试：抓取失败优先选择同公司下一件，再使用全局加权余序；Debug 新增精读目标、公司数、公司覆盖是否受上限限制、首选/实际尝试公开号和补位数，不返回正文或失败详情。
- 兼容：历史 Run 保持不可变，旧双查询计划仍可读取；报告 Schema 继续为 `landscape-report/1.1.0`，IDEA/RAG/追问未修改。
- 验证：Landscape 54 项测试和 Provider/配置/容器契约/前端/健康/日志安全/合并相关 51 项测试通过；Python 编译、Node 语法和 `git diff --check` 通过。
- 容器：应用镜像使用本次源码重建，App、PostgreSQL、Redis、MinIO 全部 healthy；`/api/health` 与 `/landscape` 可访问，容器静态资源已包含 `analysis_selection` 和“同族法域”。
- Git：本实现作为第 26 个工作单元，与第 25 个设计提交组成一对，推送 `origin/develop`。

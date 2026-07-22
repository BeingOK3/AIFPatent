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

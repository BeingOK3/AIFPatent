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

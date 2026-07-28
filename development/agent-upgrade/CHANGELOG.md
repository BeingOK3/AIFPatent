# Agent 升级开发记录

本文件采用追加方式。新的记录写在最上方，不删除历史决策。

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

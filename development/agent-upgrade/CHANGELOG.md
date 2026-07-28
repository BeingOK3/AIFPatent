# Agent 升级开发记录

本文件采用追加方式。新的记录写在最上方，不删除历史决策。

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

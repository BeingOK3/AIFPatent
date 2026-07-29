# 项目统一迁移与瘦身方案

状态：`APPROVED — direct-postgres-cutover`
提出日期：`2026-07-27`
目标分支：`feature/agent-upgrade`

本方案已获当前独立分支采用。迁移会直接切换到 PostgreSQL，不做线上兼容窗口；每个实现阶段仍单独测试、验收和留档。

## 1. 最终目标

将当前“IDEA SQLite + Landscape SQLite + PostgreSQL RAG/追问 + SQLite LangGraph checkpoint”的混合运行时收敛为：

```text
PostgreSQL（唯一业务数据库）
├── IDEA：case / run / steps / features / evidence / reports
├── Landscape：task / stage / evidence / reports
├── Corpus：immutable version / chunk / retrieval scope
├── Follow-up：thread / turn / citation / context
├── Agent：decision / action / observation / budget / approval
└── Workflow：state transition / event / lease / idempotency

S3-compatible Object Store（大正文、附件、产物；不是业务数据库）
Redis（当前移除；只有真正需要持久队列/跨进程 lease/分布式限流时再引入）
本地文件缓存（可丢失、可重建，不参与业务事实）
```

### 关键原则

1. PostgreSQL 是所有业务写入和恢复逻辑的唯一事实源。
2. SQLite 只作为一次性迁移输入、校验来源和离线回滚备份；迁移验证完成后不再作为运行依赖。
3. LangGraph checkpoint 不能成为第二套业务状态；图状态从 PostgreSQL 事件/快照重建。
4. S3 只保存不可变大对象，PostgreSQL 保存 metadata、hash、scope 和关系。
5. 删除、重试、恢复、外部调用必须有事务边界和幂等键。
6. 删除无使用点的代码前，先用 import/调用图和测试证明其不再是运行时入口。
7. Redis 本次直接移除；未来只有在明确需要持久队列、跨进程 lease 或分布式限流时才重新引入。

## 2. 为什么选 PostgreSQL

当前 PostgreSQL 已承载：

- IDEA 的 bridge/core 表；
- immutable corpus、chunks、版本 scope；
- lexical/RRF/可选 pgvector；
- report retrieval/citation；
- follow-up thread/turn/context。

继续把新能力放入 SQLite 会保留跨库同步、删除不一致、重启无法恢复和事务拆分问题。Landscape 迁入同一个 PostgreSQL 数据库后，仍可按业务表区分边界，不需要立即把所有表重命名或拆成多个服务。

本阶段不做 PostgreSQL schema namespace 大规模改名；先在同一 database 中统一表和 repository，避免迁移同时改变查询路径。

## 3. 目标代码结构

建议逐步收敛为：

```text
backend/
  app/
    factory.py              # app factory；禁止 import 时启动外部依赖
    lifespan.py
  persistence/
    postgres.py             # pool、事务、迁移锁、健康检查
    repositories/
      idea.py
      landscape.py
      corpus.py
      followup.py
      workflow.py
      agent.py
    migrations/
  workflow/
    coordinator.py          # 单一状态迁移、重试、lease、恢复
    events.py
  retrieval/
    providers/
    service.py
    evidence.py
    context.py
  agent/
    domain.py
    policy.py
    service.py
  compatibility/
    sqlite_import.py        # 迁移完成后只保留离线工具
```

实际文件名可以按现有模块渐进移动，不要求一次重命名。重点是运行时只依赖一个 `PostgresRepository/WorkflowCoordinator` 边界。

## 4. 分阶段实施

### Phase 0：冻结现状与建立可回滚基线

**不改业务行为。**

任务：

1. 给当前 SQLite IDEA、SQLite Landscape、PostgreSQL 表做完整清单：
   - 表、字段、外键、索引；
   - 每张表的读写调用点；
   - 是否属于当前运行、迁移工具、测试专用或历史兼容。
2. 修复全量测试挂起问题，建立可重复命令和超时。
3. 生成 SQLite 只读快照：
   - `sqlite_schema.json`
   - 每表 row count
   - 主键/业务 ID 列表 hash
   - 大文本 content hash
4. 为每个现有运行入口增加 contract tests：
   - IDEA 创建/取消/恢复/删除；
   - Landscape 创建/取消/恢复/删除；
   - follow-up 创建/取消/重启；
   - corpus ingest、引用和 report。
5. 不增加长期双写开关。保留离线迁移命令和备份路径即可。

验收：

- 当前模式的 contract tests 全部通过；
- 快照可重复生成；
- 每个表都有 owner、迁移策略和弃用策略；
- 任意失败都能通过迁移前的 SQLite 备份和旧提交恢复。

### Phase 1：补齐 PostgreSQL 单库 schema

**只添加表和索引，不删除 SQLite。**

1. 将现有 PostgreSQL init SQL 改为版本化迁移：
   - 每个 migration 有唯一版本；
   - 使用 PostgreSQL advisory lock；
   - 记录 checksum；
   - 不依赖“新建 volume 时才执行 init directory”。
2. 增加统一工作流表：

```text
workflow_runs
workflow_steps
workflow_events
workflow_leases
workflow_idempotency
```

字段至少包括：`run_id`、`state_version`、`status`、`step`、`attempt`、`event_type`、`payload_hash`、`idempotency_key`、时间和错误码。

3. 增加 Landscape 的 PostgreSQL 表；保留现有 domain 字段，不把 Landscape 强行塞入 IDEA JSON。
4. 为历史迁移增加：

```text
legacy_migration_runs
legacy_row_map
legacy_import_errors
```

5. 加入统一 retention/deletion 关系，明确：
   - run 删除；
   - case 删除；
   - corpus version 是否可被多个 run 引用；
   - S3 blob 的引用计数/垃圾回收；
   - follow-up child run 的父子删除策略。

验收：

- 空数据库可从头执行所有 migration；
- 已有 PostgreSQL volume 可重复升级；
- migration 失败可事务回滚；
- schema checksum 能发现手工修改。

### Phase 2：实现 SQLite → PostgreSQL 一次性迁移器

**不启用线上新功能。**

迁移顺序：

1. case 与 run 基础记录；
2. input snapshot 与 attachments metadata；
3. run steps、stage results、tool calls；
4. idea features、search queries/hits；
5. patent documents 与 run document links；
6. evidence、feature mappings、novelty/inventiveness/value/audit；
7. reports、artifacts、deletion events；
8. Landscape task/stage/evidence/report；
9. 本地 corpus 记录导入到 PostgreSQL/S3；
10. LangGraph checkpoint 不直接迁移为事实状态，只转成恢复所需的最后事件/快照。

迁移要求：

- 每批事务独立，可断点续跑；
- 所有旧 ID 写入 `legacy_row_map`；
- publication/document/version 按业务唯一键去重；
- 大文本先 hash 再上传 S3；
- 每批输出成功、跳过、冲突、失败计数；
- `--dry-run` 只读；
- `--verify` 对 row count、业务 ID、content hash、引用关系做校验；
- 任何冲突默认 fail-closed，不静默覆盖。

交付命令建议：

```bash
python tools/migrate_sqlite_to_postgres.py --dry-run
python tools/migrate_sqlite_to_postgres.py --apply --batch-size 200
python tools/migrate_sqlite_to_postgres.py --verify
```

验收门：

- dry-run 不写数据；
- apply 可重复执行；
- verify 报告所有差异；
- 随机抽样 run 的报告、Citation、Corpus version 可完整追溯；
- 迁移后的 PostgreSQL 数据可独立启动 API。

### Phase 3：读写路径直接切换到 PostgreSQL

本独立分支不需要线上维护窗口，因此不做 shadow-read、双写或长期兼容：

1. Corpus/RAG（已经主要在 PostgreSQL）；
2. Follow-up；
3. IDEA；
4. Landscape；
5. workflow/recovery；
6. deletion/retention。

执行方式：

- 先停止当前本地服务；
- 完成 SQLite 快照和 PostgreSQL 导入；
- 运行 `--verify` 和端到端回放；
- 直接把运行时读写切到 PostgreSQL；
- 删除 SQLite runtime、SQLite checkpoint 和 Redis runtime；
- SQLite 文件只保留在迁移备份目录，不进入应用镜像或启动路径。

验收：

- API contract response 与旧模式一致；
- 状态迁移、取消和重启都由 PostgreSQL 原子完成；
- PostgreSQL 不可用时 readiness 为失败，不能返回半旧半新数据；
- 删除操作能列出并处理数据库、S3、缓存、子任务关系。

### Phase 4：统一工作流和恢复

1. 引入 `WorkflowCoordinator`，集中实现：
   - allowed transitions；
   - compare-and-set；
   - retry/backoff；
   - lease；
   - idempotency；
   - startup recovery。
2. IDEA 和 Landscape 的固定线性流程不再各自维护一套 Harness/TaskManager。
3. LangGraph 只描述需要条件分支的 Agent 图；固定流程使用 coordinator 的持久步骤。
4. 如果确实需要 LangGraph checkpoint，先做 PostgreSQL checkpoint 兼容性 PoC；未验证前不引入新 checkpoint 依赖。
5. BYOK 缺失时状态设为 `WAITING_FOR_CREDENTIAL`，不能把可恢复任务直接标记为业务失败。

验收：

- 在每个步骤前、步骤中、步骤后 kill 进程，都能恢复；
- 已完成外部动作不重复执行；
- 重试次数在数据库中可解释；
- 单 worker 和多 worker 结果一致。

### Phase 5：代码瘦身与依赖收敛

PostgreSQL 直接切换并通过回放后即可执行删除，不等待线上观察期。

#### 计划移除或归档

- `backend/idea/database.py`：PostgreSQL 读写切换完成后移为离线迁移工具，再删除 runtime import。
- `backend/landscape/database.py`：迁移完成后删除 runtime import。
- `langgraph-checkpoint-sqlite`：不再使用 SQLite checkpoint 后移除。
- `backend/idea/ports.py` 中与实际 `backend/idea/vector.py` 冲突的 VectorIndex 协议，统一后删除一套。
- 未被 runtime/import 图引用的 `context_adapter.py`、FileObjectStore 和历史兼容模块。
- 旧 feature flags（deepdive/pct/value/cfp 等）和未使用 output flags。
- Redis：本次从 requirements、Compose、depends_on、health、配置和未装配适配器中删除。

#### 依赖清理候选

| 依赖 | 现状 | 处理 |
|---|---|---|
| `httpx2` | 源码未找到使用点 | Phase 0 import/lock 检查后移除 |
| `langchain` | 主要使用 `langchain_openai`/`langchain_core` | 若不需要 meta package，移除顶层包，显式保留必要包 |
| `langgraph-checkpoint-sqlite` | SQLite checkpoint 依赖 | PostgreSQL 状态切换后移除 |
| `redis[hiredis]` | 适配器存在但未装配 | 接入 queue/limiter 或整体移除 |
| `boto3` | S3 语料实际使用 | 保留 |
| `psycopg[binary]` | PostgreSQL 实际使用 | 保留 |
| `httpx[socks]` | Provider/Google probe 实际使用 | 保留，确认是否真的需要 socks extra |
| `python-multipart` | 上传接口实际使用 | 保留 |

每次删除依赖都必须执行：

```bash
backend/.venv/bin/python -m pip check
backend/.venv/bin/python -m compileall -q backend tools
backend/.venv/bin/python -m unittest discover -s backend/tests
```

并用干净环境重新安装验证，不能只依赖当前虚拟环境残留包。

### Phase 6：健壮性和安全收口

1. **启动**
   - 改为 app factory/lifespan；
   - import 不连接数据库、S3、Provider；
   - 配置错误在启动检查中给出字段级错误；
   - 本地模式明确为 `postgres-local` 或 `mock`，不再假装纯 SQLite。
2. **数据库**
   - 所有状态迁移使用 CAS；
   - 所有 repository 方法显式事务；
   - connection pool 设置 timeout、statement timeout 和断线重试；
   - migration 使用锁和 checksum。
3. **外部调用**
   - timeout、有限重试、指数退避+抖动；
   - circuit breaker 状态持久化/跨 worker；
   - 请求、抓取、子任务统一 idempotency；
   - 只记录脱敏的 query/response metadata。
4. **文件**
   - 上传限制 MIME、大小、扩展名和文件名；
   - 下载/删除按 ownership 和 artifact record 授权；
   - 产物路径不作为业务事实源。
5. **观测**
   - 每个 run/turn/action 使用 trace id；
   - readiness 分离 liveness；
   - 健康检查真实检查已启用依赖，不把 disabled 组件算作 online。
6. **数据治理**
   - 明确定义 retention；
   - 删除前 dry-run；
   - 跨数据库/对象存储删除可重试；
   - orphan S3 blob 定期扫描。

## 5. 迁移期间的版本策略

### 推荐版本序列

```text
v0  当前混合运行时
v1  PostgreSQL schema + importer + 直接切换运行时
v2  PostgreSQL workflow/recovery
v3  删除 SQLite runtime 与 sqlite checkpoint
v4  依赖和冗余代码瘦身
v5  Agent event store 与 bounded supervisor
```

每个版本必须有：

- migration checksum；
- 启动兼容矩阵；
- rollback 命令；
- 数据快照；
- 测试结果；
- `CHANGELOG.md` 记录。

## 6. 回滚策略

### 切换前

- 保留 SQLite 原文件只读副本；
- 保留 PostgreSQL logical dump；
- 保留 S3 manifest 和对象 hash；
- 禁止在没有快照的情况下执行 destructive migration。

### 切换后

- SQLite 不再作为 API 回滚运行时；
- 如果需要回滚代码，必须停写并恢复完整迁移前备份，不能把 PostgreSQL 新数据盲写回 SQLite；
- 迁移工具只支持 SQLite → PostgreSQL 单向导入。

### 永久删除前

必须由 `--verify` 输出 0 个差异，并由人工确认 retention/backup。

## 7. 关键风险与取舍

| 风险 | 处理 |
|---|---|
| PostgreSQL 迁移期间已有 SQLite 数据继续变化 | 开维护窗口或短暂只读；不做长期双写 |
| PostgreSQL checkpoint 方案未验证 | 先用自有 workflow events；LangGraph checkpoint 后置 |
| S3 对象与数据库引用不一致 | manifest、hash、引用计数和可重试 GC |
| Landscape 迁移影响较大 | 先独立表迁移，后共享 service，不强行合并业务模型 |
| Redis 依赖看似已有但实际未装配 | 明确二选一：真正接入，或删除组件 |
| 删除 legacy 代码过早 | 观察期、import 图、contract tests、git tag 后再删 |

## 8. 本方案通过后的首个实现顺序

1. `P0-1`：修复并冻结全量测试基线。
2. `P0-2`：补全表/调用点清单和 SQLite snapshot 工具。
3. `P1-1`：PostgreSQL migration runner、统一 workflow 表。
4. `P2-1`：SQLite → PostgreSQL dry-run/verify importer。
5. `P2-2`：小样本迁移并进行报告/Citation/S3 抽样回放。
6. 通过审查后才进入读路径切换。

## 9. 已确认的决策

1. PostgreSQL 是 IDEA、Landscape、追问和 Agent 的唯一业务数据库。
2. 本独立分支直接迁移，不做长期双写或线上维护兼容窗口。
3. SQLite 只用于一次性导入、校验和离线备份；迁移后删除 runtime 依赖。
4. Redis 当前没有实际运行用途，本次移除；未来有明确队列/分布式限流需求时再重新引入。
5. 固定线性流程逐步脱离 LangGraph SQLite checkpoint，优先使用 PostgreSQL workflow events。

## 10. 本次分支执行结果

截至 `2026-07-27`，本方案的直接切换部分已在独立分支完成：

- 010、020、030、035、040、050、055、060 PostgreSQL migration 已应用；
- IDEA SQLite 数据已一次性导入，主键/业务身份校验无缺失，重复 apply 不产生重复行；
- IDEA 与 Landscape runtime 均从 `AIFPATENT_POSTGRES_DSN` 装配；
- Redis、SQLite LangGraph checkpoint、容器内 SQLite 数据目录和无调用依赖已移除；
- SQLite 原文件仍作为离线备份保留，未删除，避免在人工确认前破坏迁移证据。

剩余工作属于后续 Agent 化迭代，不阻塞本次数据库统一：统一 `WorkflowCoordinator`、持久化 agent event/approval/budget，以及在 CI/真实线程池环境闭合全量测试。

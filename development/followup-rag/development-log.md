# Patent Corpus & RAG 追加式开发日志

> 本文件只记录耐久专利语料、首次评审 RAG、评审后追问、混合检索、Citation、Variant 和二次研究相关工作。
>
> 只允许在末尾追加；发现既有记录有误时，应新增更正记录，不修改历史条目。

## 2026-07-20 — AIF-FOLLOWUP-DESIGN-001

- 类型：IDEA 评审后追问、耐久专利语料与混合 RAG 架构设计。
- 产品边界：追问作为已完成 Run 的派生子系统，不进入或修改固定 11 步评审图；证据解释、技术规避候选和新研究/子 Run 使用不同模式，技术重合不得自动表述为侵权结论。
- 语料设计：专利身份与不可变内容版本分离；规范化全文使用内容寻址压缩对象并存放于独立 `workspace/patent-corpus`，禁止进入 1 GiB FIFO Cache；Run 冻结引用具体文档版本，Blob、Chunk 和 embedding 分层去重。
- RAG 设计：以源 Run 的 10～40 篇深读文献为强制范围；SQLite FTS5 提供词法召回，`VectorIndex` 抽象默认对 Run 范围执行精确余弦检索，使用 RRF 融合并支持可选 Reranker，规模扩大后可切换 Qdrant/pgvector。
- 证据门禁：模型只引用当前 Citation Packet 的 C1..Cn 别名；后端校验公开号、Version、Chunk、原文、哈希和回答路径；没有证据返回 `INSUFFICIENT_EVIDENCE`，需要新检索时等待用户显式创建研究任务或 child Run。
- 实施路线：先完成 Corpus Schema/Store/Ingest/Chunk，再交付 FTS 证据问答和 Citation Verifier，之后加入 embedding、混合检索、Variant 和区别特征二次检索；第一验收切片要求 FIFO 清空后仍可依据耐久原文追问且原报告 Manifest 不变。
- 涉及文件：`development/followup-rag/architecture.md`、`development/followup-rag/README.md`、`development/followup-rag/development-log.md`、根 `README.md`。
- 验证：架构文档章节、Markdown fence、内部链接和 `git diff --check` 通过；本 Work Unit 只建立后续开发基线，不实现运行时代码。

## 2026-07-20 — AIF-RAG-DIRECTION-002

- 类型：共享 RAG、生产数据底座和公开访问方向修订。
- 更正关系：本记录取代 `AIF-FOLLOWUP-DESIGN-001` 中“SQLite FTS5 + sqlite_exact 作为新子系统 MVP”和“RAG 只面向评审后追问”的方向；历史记录保留用于审计。
- 共享管线：外部查询规划和 Google Patents/EXA 继续负责候选召回；只对深度分析文献抓取全文，随后完成 Version/Blob 持久化、专利结构化 Chunk、词法/向量索引。首次报告按 `F_i × D_j` 检索证据，后续追问复用同一 Version、Chunk、Retriever 和 Citation。
- 报告门禁：摘要和所有独立权利要求必须被评估；命中的从属权利要求补齐父权利要求链；说明书使用混合 RAG；`NOT_DISCLOSED` 不得只由普通 Top-K 未命中推出；新颖性仍禁止跨文献拼接。
- 全文保存：普通候选只保存元数据，深读文献保存规范化摘要、完整权利要求和说明书；稳定正文哈希排除 `retrieved_at`、来源 URL、请求时间和任务 ID 等易变字段；压缩对象与数据库元数据分离并跨 Run 去重。
- 生产底座：PostgreSQL 为业务事实来源，PostgreSQL FTS/`pg_trgm` 负责词法召回，pgvector 负责向量召回；S3/MinIO 保存内容寻址全文；Redis 承担 Job、分布式租约和跨 Worker Provider 实时限流，持久熔断事实进入 PostgreSQL。当前不引入 Qdrant/Elasticsearch。
- 并发修订：当前进程内 Google 锁不满足多 Worker 部署；目标限流器按“出口路由 + Origin”跨进程全局串行，并共享冷却和熔断。
- 访问模型：当前采用 `public_shared`，不实现用户、组织、分组、租户或内容 ACL；所有访客可查看全部 Case、Run、报告、Thread、Turn 和 Citation。BYOK、数据库凭证和运维能力仍严格非公开，公开页面必须提示不要提交秘密或敏感内容。
- 实施顺序：先建立 PostgreSQL/pgvector、Redis、ObjectStore 和 SQLite 数据迁移基线，再实现耐久 Corpus、首次报告词法 RAG、pgvector 混合检索与追问，最后扩展 Variant/新研究。
- 涉及文件：`development/followup-rag/architecture.md`、`development/followup-rag/README.md`、`development/followup-rag/development-log.md`。
- 验证：待完成术语扫描、Markdown fence、内部链接和 `git diff --check`；本记录只更新开发基线，不代表运行时代码已经完成迁移。

## 2026-07-20 — AIF-RAG-DIRECTION-002-VERIFY

- 类型：文档验证补记。
- 结果：三份开发文档 Markdown fence 数量为偶数，无行尾空白；前置文档 `docs/aifpatent-architecture.md` 存在；架构基线中未残留“SQLite 是新 RAG 业务事实来源”“FTS5/sqlite_exact 是新 MVP”等旧方向表述。
- Git：`development/` 当前整体为未跟踪目录，因此普通 `git diff --check` 不覆盖这些新文件；已对三份实际文件独立执行格式扫描，工作树中其他已有代码与文档改动未被本 Work Unit 修改。

## 2026-07-21 — AIF-CONTEXT-DESIGN-003

- 类型：模型上下文所有权、LangChain 复用边界与实施计划补充。
- 上下文所有权：专利模型上下文由项目的版本化 `ContextAssembler` 生成；新增目标逻辑表 `model_context_manifests`，记录 Scope、Version/Chunk、Citation 绑定、预算/配额、Prompt/Retriever/TokenCounter 版本、排除项、限制和 `context_hash`，业务 PostgreSQL 是装配审计来源。
- LangChain 边界：继续复用 `ChatOpenAI`、标准消息、Prompt 渲染和经项目接口封装的 Token helper；不使用 Agent Memory、Long-term Memory、自动裁剪/摘要或 Retriever/VectorStore 作为专利事实、上下文范围、证据优先级和引用合法性的权威实现。
- 状态边界：LangGraph Checkpoint 继续只保存轻量执行状态，不保存完整 Messages State、问题、专利正文、Citation Packet 或最终模型上下文；完整 Prompt 不进入默认调试日志。
- 对话语义：原始 Thread/Turn 不可变追加；派生摘要必须绑定来源 Turn 和模型/版本并可验证，不能覆盖历史，也不能替代每轮重新检索的专利证据。
- 实施路线：Phase 3 新增 `IDEA-CONTEXT-CONTRACT-001`，Phase 4 新增 `IDEA-FOLLOWUP-CONTEXT-001`，并补充确定性重建、预算溢出、强制证据配额、适配层升级和 Checkpoint 泄漏测试。
- 涉及文件：`development/followup-rag/architecture.md`、`development/followup-rag/README.md`、`development/followup-rag/development-log.md`。
- 验证：待完成术语、编号、JSON 示例、Markdown fence、内部链接和 diff 格式检查；本 Work Unit 只更新后续开发计划，不修改当前运行时代码。

## 2026-07-21 — AIF-CONTEXT-DESIGN-003-VERIFY

- 类型：文档验证补记。
- 结果：配置 JSON 示例可解析；架构文档 Markdown fence 成对；三份开发文档无行尾空白；上下文逻辑表、Phase 3/4 Work Unit、固定架构决策和推荐开发切片相互对应；前置架构文档存在。
- Git：`git diff --check` 通过；本次仅修改 `development/followup-rag/` 下三份计划文档，未修改运行时代码。

## 2026-07-21 — IDEA-BOUNDARIES-001

- 类型：Phase 0 功能门禁与基础设施端口契约。
- 功能门禁：新增 `patent_corpus`、`initial_review_rag`、`followup_rag`，默认全部关闭；首次评审 RAG 必须依赖 Corpus，追问 RAG 必须依赖首次评审 RAG，非法组合在配置加载时 fail closed。
- 端口契约：新增泛型不可变记录 `Repository`、内容寻址 `ObjectStore`、带租约 `JobQueue`、跨进程冷却 `DistributedLimiter` 和强制 Version Scope 的 `VectorIndex` Protocol，以及对应不可变 DTO。
- 安全边界：对象哈希只接受规范化 SHA-256；向量和分数拒绝 NaN/Infinity；向量查询必须携带非空允许 Version 集合，禁止无范围全库搜索。
- 涉及文件：`backend/idea/config.py`、`backend/idea/ports.py`、`backend/tests/test_config.py`、`backend/tests/test_ports.py`、`config/ai4patent.json`、`config/ai4patent.schema.json`、本日志。
- 验证：待运行配置、端口、完整离线测试、编译和 diff 格式检查。

## 2026-07-21 — IDEA-BOUNDARIES-001-VERIFY

- 类型：Phase 0 功能门禁与端口契约验证补记。
- 结果：配置与端口目标测试 11 项通过；完整离线套件 195 项通过；Python compileall、两份配置 JSON 语法和 `git diff --check` 通过。
- 环境说明：默认沙箱禁止 TestClient/临时 HTTP fixture 所需的本机通信并造成假性等待；在受控测试权限下 API 12 项、Execution 10 项和 CLI 6 项均通过，确认不是代码回归。
- 安全检查：默认配置中的三个新功能继续关闭；配置快照、测试输出和 Git diff 不包含模型 API Key。

## 2026-07-21 — IDEA-INFRA-DEV-001

- 类型：Phase 0 目标依赖开发模板与管理 CLI。
- 依赖模板：新增 PostgreSQL 17 + pgvector 0.8.2、Redis 8.4.4 和 S3-compatible ObjectStore Compose 服务；端口默认仅绑定回环地址，数据使用独立命名卷，PostgreSQL 首次初始化 `vector`/`pg_trgm`。
- MinIO 来源：社区服务器从固定安全修复 tag `RELEASE.2025-10-15T17-29-55Z` 源码构建，不依赖更早的预构建服务镜像。
- 凭证语义：`tools/rag_infra.py up` 在缺失时以排他创建、随机值和 `0600` 权限生成 Git 忽略的 `deploy/rag/rag.env`；不回显值、不覆盖既有文件，placeholder、缺失值或过宽权限 fail closed。
- 运维语义：提供 `init/up/status/check/down`；`up` 等待健康检查，`down` 默认保留卷，故意不提供隐式 reset/删除卷操作。
- 当前边界：本模板不切换现有 SQLite 运行时，三个 RAG feature flag 继续默认关闭。
- 涉及文件：`deploy/rag/`、`tools/rag_infra.py`、`backend/tests/test_rag_infra.py`、`.gitignore`、根 `README.md`、本日志。
- 验证：待运行 CLI/模板目标测试、完整离线套件、编译和格式检查；当前主机未安装 Docker，Compose 容器运行态验证需在具备 Docker 的环境完成。

## 2026-07-21 — IDEA-INFRA-DEV-001-VERIFY

- 类型：Phase 0 目标依赖模板验证补记。
- 结果：RAG infrastructure 目标测试 5 项通过；完整离线套件 200 项通过；Compose YAML 可解析；CLI 编译和帮助输出通过；`git diff --check` 通过。
- 安全检查：`rag.env` 被 Git 忽略，随机凭证只在本地文件创建时生成且权限为 `0600`；模板未包含密码，`down` 不带卷删除参数。
- 环境限制：主机未安装 Docker/Compose，因此未执行镜像构建、容器健康检查或 PostgreSQL/Redis/MinIO 运行态验证。

## 2026-07-21 — IDEA-OBJECT-STORE-001

- 类型：Phase 1 本地 ObjectStore 基准适配器。
- 实现：新增 `FileObjectStore`，实现 `ObjectStore` 端口的内容寻址写入、读取和 stat；写入先校验 SHA-256，再通过同目录临时文件、fsync 和不可覆盖 hard-link 原子落盘。
- 安全语义：拒绝绝对路径、`..`、反斜杠、符号链接和越出 root 的父目录；对象文件权限为 `0600`；同一 Key 不允许不同正文覆盖，允许同哈希幂等复用。
- 兼容边界：本地适配器只作为开发/测试基准，生产 S3/MinIO 适配器必须保持相同不可变 Key、哈希和条件写契约。
- 涉及文件：`backend/idea/object_store.py`、`backend/tests/test_object_store.py`、本日志。
- 验证：待运行 ObjectStore 目标测试、完整离线套件、编译和格式检查。

## 2026-07-21 — IDEA-OBJECT-STORE-001-VERIFY

- 类型：Phase 1 本地 ObjectStore 验证补记。
- 结果：ObjectStore、RAG infra、配置和端口目标测试共 21 项通过；完整离线套件 205 项通过；compileall 和 `git diff --check` 通过。
- 安全检查：不同正文不能覆盖同一 Key；临时文件、对象文件和元数据文件均不向组/其他用户开放；符号链接、路径穿越和错误哈希均 fail closed。

## 2026-07-21 — IDEA-SQLITE-EXPORT-001

- 类型：Phase 0 SQLite→PostgreSQL 迁移演练基线。
- 实现：新增只读 SQLite 导出器和 `tools/export_sqlite.py`；以 `mode=ro` 打开源库，先执行 integrity/foreign-key 检查，再按表名、列定义和规范化行值排序导出，记录 SQLite `user_version` 和确定性 `export_hash`。
- 写入语义：导出文件使用同目录临时文件、fsync、不可覆盖 hard-link 和 `0600` 权限；已有目标文件一律拒绝覆盖；工具不修改源数据库、不删除源数据、不输出行内容。
- 迁移边界：导出是 PostgreSQL 导入/哈希核验的只读输入，不代表生产切换，也不改变当前 SQLite 运行时。
- 涉及文件：`backend/idea/sqlite_export.py`、`tools/export_sqlite.py`、`backend/tests/test_sqlite_export.py`、本日志。
- 验证：待运行 SQLite export 目标测试、完整离线套件、编译和格式检查。

## 2026-07-21 — IDEA-SQLITE-EXPORT-001-VERIFY

- 类型：Phase 0 SQLite 导出验证补记。
- 结果：SQLite export 目标测试 4 项通过；完整离线套件 209 项通过；CLI 帮助、compileall 和 `git diff --check` 通过。
- 安全检查：源数据库 mtime 在导出前后不变；导出文件拒绝覆盖、使用 `0600` 权限，输出只包含表/列/行哈希所需数据，不打印行正文。

## 2026-07-21 — IDEA-PG-CORE-001

- 类型：Phase 1 PostgreSQL 核心业务 Schema 桥接基线。
- 实现：新增 `010_core_schema.sql`，覆盖 Case/Run/Run Input/Step、Stage/Tool Call、查询/命中、专利元数据、Evidence/Feature Mapping、新颖性/创造性/价值/审计、报告/Artifact、熔断和 `model_context_manifests`。
- 兼容语义：当前桥接阶段保留 SQLite 的 TEXT ID 和毫秒 BIGINT 时间，JSON 字段升级为 JSONB；待导入核验通过后再评估 UUID/TIMESTAMPTZ 的生产迁移，不在此阶段静默转换数据。
- 不可变性：PostgreSQL trigger 保留 Run 固定字段和 Run Input write-once 约束；初始化脚本无数据删除命令，可重复执行并记录 `aifpatent_schema_migrations`。
- 涉及文件：`deploy/rag/postgres-init/010_core_schema.sql`、`backend/tests/test_postgres_schema.py`、本日志。
- 验证：待运行 Schema 静态目标测试、完整离线套件、编译和格式检查；当前主机无 PostgreSQL 容器，未执行数据库运行态迁移。

## 2026-07-21 — IDEA-PG-CORE-001-VERIFY

- 类型：Phase 1 PostgreSQL Schema 验证补记。
- 结果：Schema、SQLite export 和 ObjectStore 目标测试共 12 项通过；完整离线套件 212 项通过；compileall 和 `git diff --check` 通过。
- 环境限制：主机没有 `psql`/Docker，因此 SQL 仅完成静态契约检查，未声称已经通过 PostgreSQL 运行态迁移。

## 2026-07-21 — IDEA-PROVIDER-LIMITER-001

- 类型：Phase 1 Redis 分布式 Provider 限流基线。
- 实现：新增 `RedisDistributedLimiter`，用注入式 `redis.asyncio` 客户端和 Lua 脚本实现 Lease acquire/release、跨 Worker cooldown 和延迟时间；Redis Key 分离为 lease/cooldown，释放必须匹配 lease token，延迟不会缩短已有冷却。
- 连接边界：不创建全局客户端；生产通过 `from_url` 显式注入 redis-py asyncio 客户端，应用生命周期负责连接池关闭；当前模块导入不要求 Redis 已安装，离线测试可使用 fake/Mock。
- 依赖：`redis[hiredis]>=6.2,<7`，适配 Python 3.10+ 与目标 Redis 8 开发栈；凭证只来自部署 Secret/URL，不写入配置 JSON。
- 涉及文件：`backend/idea/redis_limiter.py`、`backend/tests/test_redis_limiter.py`、`backend/requirements.txt`、本日志。
- 验证：待运行 Redis limiter 目标测试、完整离线套件、编译和格式检查；当前主机无 Redis/Docker，未执行运行态租约竞争测试。

## 2026-07-21 — IDEA-PROVIDER-LIMITER-001-VERIFY

- 类型：Phase 1 Redis 限流验证补记。
- 结果：Redis limiter 与端口目标测试共 7 项通过；完整离线套件 216 项通过；compileall 和 `git diff --check` 通过。
- 环境限制：未安装 Redis/Docker，未声称通过真实多 Worker 租约竞争；Lua 脚本参数、key 隔离、lease token 和 cooldown 延迟逻辑已由 Mock 契约覆盖。

## 2026-07-21 — IDEA-REDIS-JOBS-001

- 类型：Phase 1 Redis JobQueue 基线。
- 实现：新增 Redis List/Hash JobQueue，使用 Lua 原子完成幂等入队、pending→processing claim、lease 校验、完成、可重试失败和取消；JobLease 到期或 token 不匹配时拒绝状态变更。
- 安全边界：JobRequest 和完成结果递归拒绝 API Key、Token、Password、Authorization、Secret 字段；Redis payload 只保存任务数据，不保存 BYOK；错误码要求单行。
- 恢复语义：可重试失败重新回到 pending；不可重试失败进入 FAILED；取消同时清理 pending/processing 和 lease，已完成/失败/取消任务不可重复取消。
- 连接边界：复用 `redis.asyncio` 注入式客户端，不创建全局连接；Redis connection pool 生命周期由应用装配层负责关闭。
- 涉及文件：`backend/idea/redis_job_queue.py`、`backend/tests/test_redis_job_queue.py`、本日志。
- 验证：待运行 JobQueue 目标测试、完整离线套件、编译和格式检查；当前无 Redis/Docker，未执行真实 lease 竞争。

## 2026-07-21 — IDEA-REDIS-JOBS-001-VERIFY

- 类型：Phase 1 Redis JobQueue 验证补记。
- 结果：JobQueue 与 Redis limiter 目标测试共 8 项通过；完整离线套件 220 项通过；compileall 和 `git diff --check` 通过。
- 安全检查：claim/complete/fail 使用 job-scoped lease token；payload/结果递归凭证字段拒绝；未把 Redis URL、密码或 BYOK 写入测试输出和日志。
- 环境限制：未安装 Redis/Docker，未声称通过真实 Redis 原子脚本、租约过期和多 Worker 竞争；这些仍是目标环境验收项。

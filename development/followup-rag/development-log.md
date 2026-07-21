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

## 2026-07-21 — IDEA-CORPUS-VERSION-001

- 类型：Phase 1 专利语料不可变版本基线。
- 实现：新增 `PatentCorpusService`，将已验证的 `FetchedDocument` 规范化为稳定 JSON，计算内容 SHA-256，写入 `ObjectStore` 内容寻址 Blob，并以确定性 Version ID 写入版本仓储。
- 幂等语义：相同公开号、语言和正文哈希复用既有 Version/Blob；正文变化创建新 Version，禁止覆盖旧对象；`snapshot_hash` 对排序后的 Version 清单确定性计算。
- 完整性：读取 Version 前检查 READY 状态和 Blob 哈希；缺失或损坏对象 fail closed。Chunk、FTS、embedding 和 Run 绑定留给后续 Work Unit。
- 涉及文件：`backend/idea/corpus.py`、`backend/idea/__init__.py`、`backend/tests/test_corpus.py`、本日志。
- 验证：待运行 Corpus 目标测试、完整离线套件、编译和 diff 格式检查。

## 2026-07-21 — IDEA-CORPUS-VERSION-001-VERIFY

- 类型：语料版本基线验证补记。
- 结果：Corpus 目标测试 4 项通过；compileall 和 `git diff --check` 通过；当前工作区未写入 API Key 或对象正文日志。
- 环境限制：本次使用本地 FileObjectStore 与内存 Repository 验证领域契约，尚未连接 PostgreSQL/MinIO 运行态。

## 2026-07-21 — IDEA-CORPUS-CHUNK-001

- 类型：Phase 1 版本内结构化 Chunk 基线。
- 实现：新增 `PatentChunker`，按摘要、单项权利要求和说明书段落生成结构化 Chunk；记录章节标签、Claim 依赖、字符偏移、文本哈希、词数和 `chunker_version`。
- 稳定性：Chunk ID 由 `Version ID + section + label + offsets + chunker_version` 计算；同一 Version 重建结果一致，不同 Version 不复用 ID；不修改源正文，不做跨文献拼接。
- 范围：本切片尚未写 PostgreSQL FTS/pgvector，也未宣称完成 token 级分块；后续按配置补充超长段落滑窗和索引持久化。
- 涉及文件：`backend/idea/chunks.py`、`backend/tests/test_chunks.py`、`backend/idea/__init__.py`、本日志。
- 验证：待运行 Chunk 目标测试、完整离线套件、编译和 diff 格式检查。

## 2026-07-21 — IDEA-CORPUS-CHUNK-001-VERIFY

- 类型：结构化 Chunk 验证补记。
- 结果：Chunk 目标测试 3 项通过；Chunk ID、父权利要求链和 Version scope 约束通过；compileall 和 `git diff --check` 通过。
- 环境限制：索引持久化和运行态 PostgreSQL/pgvector 尚未接入。

## 2026-07-21 — IDEA-CONTEXT-ASSEMBLER-001

- 类型：Phase 3 领域上下文装配基线。
- 实现：新增 `ContextAssembler` 和 `AssembledModelContext`；按固定消息顺序、输入预算和候选 Chunk 顺序生成证据消息、Citation alias、选中/排除清单、限制项及确定性 `context_hash`。
- 所有权：模型上下文选择、预算裁剪、Citation 绑定和哈希属于项目领域层；LangChain 只能消费最终 `ModelMessage`，不能使用 Agent Memory 或自动裁剪改变 Manifest。
- 安全语义：证据明确标记为不可信数据；API Key 等凭证不进入消息；预算无法容纳任何 Chunk 时 fail closed，预算排除显式写入 Manifest。
- 涉及文件：`backend/idea/context.py`、`backend/tests/test_context.py`、`backend/idea/__init__.py`、本日志。
- 验证：待运行 Context 目标测试、完整离线套件、编译和 diff 格式检查。

## 2026-07-21 — IDEA-CONTEXT-ASSEMBLER-001-VERIFY

- 类型：上下文装配验证补记。
- 结果：Context 目标测试 3 项通过；相同输入哈希稳定、Citation alias 有范围绑定、预算排除可审计、无可用证据时 fail closed；compileall 和 `git diff --check` 通过。
- 环境限制：本切片只实现领域 Contract，尚未接入 PostgreSQL Manifest Repository 或 LangChain 运行时适配器。

## 2026-07-21 — IDEA-LANGCHAIN-ADAPTER-001

- 类型：Phase 3 LangChain 消息适配边界。
- 实现：新增可选依赖的 `to_langchain_messages`；仅将 `AssembledModelContext.messages` 映射为 `SystemMessage`/`HumanMessage`，并提供无依赖的字典表示用于测试与其他客户端。
- 边界：`langchain-core` 采用延迟导入；未安装时抛出明确的 `LangChainAdapterUnavailable`。适配器不执行检索、Memory、自动裁剪、摘要或哈希重算。
- 涉及文件：`backend/idea/context_adapter.py`、`backend/tests/test_context_adapter.py`、`backend/idea/__init__.py`、本日志。
- 验证：待运行适配器与 Context 目标测试、完整离线套件、编译和 diff 格式检查。

## 2026-07-21 — IDEA-LANGCHAIN-ADAPTER-001-VERIFY

- 类型：LangChain 适配边界验证补记。
- 结果：适配器与 Context 目标测试 5 项通过；无 LangChain 安装时保持延迟、明确失败；若依赖存在则消息角色与内容逐条保持一致；compileall 和 `git diff --check` 通过。
- 安全检查：适配器只传递已装配消息，不读取配置密钥，不生成或修改上下文 Manifest。

## 2026-07-21 — IDEA-INFRA-GOPROXY-001

- 类型：Phase 0/1 开发基础设施构建兼容性修正。
- 实现：MinIO 源码构建增加可覆盖的 `GOPROXY` build arg；Compose 默认保持 `proxy.golang.org`，受限网络环境可通过 `AIFPATENT_GOPROXY` 指定可访问的 Go module proxy。
- 边界：仅影响 Docker build 阶段，不改变 MinIO 版本、运行时镜像、业务服务或 Python 依赖；基础镜像仍按 Dockerfile digest 锁定。
- 涉及文件：`deploy/rag/Dockerfile.minio`、`deploy/rag/compose.yml`、本日志。
- 验证：待用当前网络的可达 Go proxy 完成 MinIO 构建和三服务运行态验收。

## 2026-07-21 — IDEA-INFRA-BUILD-NETWORK-001

- 类型：开发环境 Docker Build 网络兼容性修正。
- 实现：MinIO Dockerfile 的源码下载和运行时依赖安装步骤使用 BuildKit `--network=host`，仅用于构建阶段访问 WSL 宿主代理；最终对象存储容器仍由 Compose 使用隔离网络和回环端口。
- 安全边界：没有扩大最终容器端口、卷或运行时网络；MinIO 版本和基础镜像 digest 不变。
- 涉及文件：`deploy/rag/Dockerfile.minio`、本日志。
- 验证：待重新构建 MinIO 并执行对象存储健康、持久化和三服务验收。

## 2026-07-21 — IDEA-INFRA-MINIO-RUNTIME-001

- 类型：MinIO 开发镜像网络减负修正。
- 实现：移除运行时 Debian apt 安装的 `curl`，健康检查改用 MinIO 自带 `--version`；宿主机验收仍通过 `http://127.0.0.1:9000/minio/health/live` 验证真实 HTTP 服务。
- 影响：运行镜像不再依赖构建时 Debian 软件源，保持固定 MinIO 源码版本、非 root 用户和数据卷不变。
- 涉及文件：`deploy/rag/Dockerfile.minio`、`deploy/rag/compose.yml`、本日志。
- 验证：待完成最终镜像构建、容器健康检查和对象持久化冒烟。

## 2026-07-21 — IDEA-INFRA-RUNTIME-001-VERIFY

- 类型：本地 RAG 依赖运行态验收补记。
- 结果：PostgreSQL、Redis、MinIO 三个 Compose 服务均为 `healthy`；PostgreSQL 真实查询确认 `vector`/`pg_trgm` 和 24 张 public 表；Redis 密码鉴权返回 `PONG`；MinIO `/minio/health/live` 和 Console 端口通过；SigV4 建桶、写入、读回哈希校验和清理通过。
- 构建说明：基础镜像按 Dockerfile 锁定 digest 加载；MinIO 固定源码版本构建成功；当前网络使用可覆盖的 Go module proxy 完成构建。
- Python 验证：完整离线套件 232 项通过；compileall 和 `git diff --check` 通过。
- 安全检查：测试凭证只从 `deploy/rag/rag.env` 注入当前进程，不打印、不写入对象、不进入 Git；测试桶和对象已清理。

## 2026-07-21 — IDEA-CORPUS-SCHEMA-001

- 类型：Phase 2 耐久 Corpus 的 PostgreSQL Schema。
- 实现：新增 `020_corpus_schema.sql`，建立内容寻址 Blob、不可变专利 Version、来源记录、Run→Version 冻结绑定、结构化 Chunk、embedding profile/vector、Chunk 关联和首次报告检索命中审计表。
- 不可变边界：正文/Chunk/向量以 SHA-256、Version、Chunker 和 embedding profile 版本区分；Run 绑定以 `(run_id, document_id)` 固定具体 Version；Schema 不覆盖旧的可更新 `patent_documents` 缓存。
- 安全边界：哈希、状态、字节数、偏移、排序名次和分数均有数据库约束；外键删除策略禁止静默删除被引用语料；迁移可重复执行且不含数据删除命令。
- 涉及文件：`deploy/rag/postgres-init/020_corpus_schema.sql`、`backend/tests/test_postgres_schema.py`。
- 验证：待运行 Schema 目标测试和完整离线套件；已有 PostgreSQL 容器需要显式执行该迁移，初始化卷不会自动重跑旧 init 脚本。

## 2026-07-21 — IDEA-CORPUS-SCHEMA-001-MIGRATE

- 类型：已有 PostgreSQL 卷的增量迁移入口。
- 实现：`tools/rag_infra.py migrate` 通过 Compose 在 PostgreSQL 容器内执行 `020_corpus_schema.sql`，使用容器已有的 `POSTGRES_USER/POSTGRES_DB` 环境变量，不把凭证拼进命令或输出；迁移 SQL 具备幂等记录，可重复执行。
- 运维边界：命令不删除卷、不重建服务，专门解决 init 目录只在首次初始化时执行的问题。
- 涉及文件：`tools/rag_infra.py`、`backend/tests/test_rag_infra.py`、`deploy/rag/README.md`。
- 验证：待运行管理 CLI 目标测试；真实容器迁移需在 Docker 用户组已生效的终端执行 `tools/rag_infra.py migrate`。

## 2026-07-21 — IDEA-APP-CONTAINER-001

- 类型：跨环境部署的非 root 应用镜像基线。
- 实现：新增固定 Python 3.12.13 Bookworm 官方镜像 digest 的应用 Dockerfile；安装后端依赖并只复制运行所需的 backend/frontend/config；Uvicorn 固定单 Worker，保持当前 SQLite、进程内任务和 BYOK 生命周期语义。
- 安全边界：容器以 UID/GID 10001 运行；构建上下文排除 Git、虚拟环境、运行数据、报告、日志、`.env` 和本地 RAG 凭证；镜像不包含模型 API Key；健康检查使用 Python 标准库，不增加 apt 运行依赖。
- 持久化边界：预创建 data、workspace 和 logs 目录，后续 Compose 必须将它们映射为持久卷；本工作单元尚未改变现有宿主机启动方式。
- 涉及文件：`deploy/app/Dockerfile`、`.dockerignore`、`backend/tests/test_app_container.py`。
- 验证：待运行容器静态契约、完整离线测试和真实镜像构建。

## 2026-07-21 — IDEA-APP-CONTAINER-001-VERIFY

- 类型：应用镜像运行态验收补记。
- 结果：固定 Python 基础镜像和依赖安装成功；临时容器以 `10001:10001` 启动，`/api/health` 返回 200，OpenAPI 可读取；无模型 Token 时健康结果按现有语义为 `degraded`，不影响进程启动；测试容器已清理。
- 网络说明：PyPI 官方源在当前网络约 15 KB/s，改用构建参数 `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` 后依赖下载恢复到 MB/s 级；该参数只影响本地构建下载，不写入镜像运行环境。

## 2026-07-21 — IDEA-APP-COMPOSE-001

- 类型：应用容器接入本地 Compose。
- 实现：Compose 新增 `app` 服务，依赖 PostgreSQL/Redis/MinIO 健康状态后启动；应用只绑定回环端口，使用独立 `app-data`、`app-workspace` 和 `app-logs` 命名卷，应用健康检查复用 `/api/health`。
- 配置边界：新增应用端口和构建期 `PIP_INDEX_URL` 非秘密配置；`rag.env` 仍只在本地生成且被 Git 忽略，Compose 不注入模型 API Key。
- 运行边界：应用仍使用 SQLite、LangGraph SQLite Checkpoint 和单 Worker；RAG 容器只是目标依赖，功能开关继续关闭。
- 涉及文件：`deploy/rag/compose.yml`、`deploy/rag/rag.env.example`、`tools/rag_infra.py`、`deploy/rag/README.md`、根 `README.md`、`backend/tests/test_rag_infra.py`。
- 验证：待执行 Compose 配置检查和三服务加应用的真实运行态验收。

## 2026-07-21 — IDEA-APP-COMPOSE-001-VERIFY

- 类型：应用 Compose 运行态验收补记。
- 结果：Compose 配置解析通过；PostgreSQL、Redis、MinIO 均保持 `healthy`，应用进程正常启动并绑定 `127.0.0.1:8001`。
- 更正：首次使用 `/api/health` 作为容器健康检查时，由于该接口会执行外部 Provider/模型健康语义，3 秒内可能超时并被误判为 unhealthy；健康检查改用本地 `/openapi.json` 进程探针，业务健康状态仍由 `/api/health` 对外报告。
- 状态：修正后的镜像和 Compose 栈待重新构建/启动后完成最终健康验收。

## 2026-07-21 — IDEA-APP-COMPOSE-001-RUNTIME-VERIFY

- 类型：应用 Compose 最终运行态验收。
- 结果：四个服务均为 `healthy`；应用 OpenAPI 和前端首页可读取，容器以 `10001:10001` 运行；应用工作区命名卷写入临时标记、重启 app 后读回并清理成功。
- 端口：应用 `127.0.0.1:8001`，PostgreSQL `127.0.0.1:5432`，Redis `127.0.0.1:6379`，MinIO `127.0.0.1:9000/9001`。
- 更正结果：健康检查改用进程级 `/openapi.json`，业务 `/api/health` 继续保留用于真实组件健康状态；未把模型 API Key 写入镜像、Compose 或卷。

## 2026-07-21 — IDEA-APP-COMPOSE-001-BUILD-ENTRYPOINT-VERIFY

- 类型：统一 Compose 构建入口兼容性修正。
- 问题：Docker Compose 当前版本即使设置 `COMPOSE_BAKE=false` 仍通过 Buildx Bake，无法授权 MinIO Dockerfile 所需的 `network.host`，导致 `tools/rag_infra.py up` 在全新构建时失败。
- 修正：`up` 现在先用 `docker buildx build --allow network.host --network host` 构建并加载 app 与 MinIO，再执行 `docker compose up --no-build --wait`；构建参数只读取公开的 Python/Go 镜像源和版本，不读取凭证。
- 验证：使用 PyPI 清华镜像和 `goproxy.cn` 完整执行 `tools/rag_infra.py up`，应用、PostgreSQL、Redis、MinIO 全部 `healthy`。

## 2026-07-21 — IDEA-CORPUS-STORE-001

- 类型：Phase 2 耐久 Corpus Store 的 S3/MinIO ObjectStore 适配器。
- 实现：新增 `S3ObjectStore`，通过延迟导入 boto3 兼容 MinIO/S3；所有阻塞 SDK 调用放入线程，保持现有异步 `ObjectStore` 端口；支持内容哈希 metadata、条件写、幂等复用、读取后哈希校验、对象状态查询和 Bucket healthcheck。
- 安全边界：凭证只由构造参数注入，不进入日志或对象正文；对象 Key 拒绝绝对路径和穿越；缺失/非法 SHA-256 metadata 的对象 fail closed；`IfNoneMatch=*` 防止不同正文覆盖同一 Key。
- 依赖：新增 `psycopg[binary]` 和 `boto3`，为后续 PostgreSQL Repository 与 MinIO 适配提供运行时依赖；模型 API 本切片不参与。
- 涉及文件：`backend/idea/s3_object_store.py`、`backend/tests/test_s3_object_store.py`、`backend/requirements.txt`、`backend/idea/__init__.py`。
- 验证：待运行 S3 适配器目标测试、完整离线套件和真实 MinIO Bucket/Object 往返测试。

## 2026-07-21 — IDEA-CORPUS-STORE-001-POSTGRES

- 类型：Phase 2 PostgreSQL Corpus Version Repository。
- 实现：新增 `PostgreSQLCorpusVersionRepository`，把现有 `patent_documents` 的稳定 `document_id`、内容寻址 Blob 元数据和不可变 `patent_document_versions` 连接起来；写入使用 `ON CONFLICT DO NOTHING`，读取同时校验 Version、Blob 和原始专利身份关系。
- 兼容语义：`CorpusVersion.document_id` 保持可选以兼容现有本地内存测试；PostgreSQL 持久化时优先使用它，否则按公开号/语言解析已有 `patent_documents`。
- 安全边界：没有已有专利元数据行时拒绝 Version 写入；provider 只进入可审计 metadata JSON；DSN、用户名和密码只通过构造参数/环境注入。
- 涉及文件：`backend/idea/postgres_corpus.py`、`backend/idea/corpus.py`、`backend/tests/test_postgres_corpus.py`、`backend/requirements.txt`、`backend/idea/__init__.py`。
- 验证：待运行目标测试、完整离线套件和真实 PostgreSQL/MinIO Corpus 往返验收；模型 API 本切片不参与。

## 2026-07-21 — IDEA-CORPUS-STORE-001-VERIFY

- 类型：Corpus Store 运行态验收补记。
- 结果：S3ObjectStore 目标测试 3 项、PostgreSQL Repository Contract 测试 3 项通过；真实 MinIO 创建临时 Bucket、幂等写入、读取和 SHA-256 校验通过；真实 PostgreSQL Repository 插入、幂等写入、读取和 healthcheck 通过。
- 端到端：临时 `FetchedDocument` 经 `PatentCorpusService` 写入 MinIO 和 PostgreSQL，`get_ready` 与 `snapshot_hash` 成功；测试 Bucket、对象、Version、Blob 和专利元数据均按随机 ID 精确清理。
- Python 验证：完整离线套件 246 项通过；compileall 和 `git diff --check` 通过。
- 模型边界：本 Work Unit 不调用模型 API；DeepSeek API 留给后续首次报告 RAG/Context 接入的真实模型验收。

## 2026-07-21 — IDEA-CORPUS-STORE-001-CONTAINER-VERIFY

- 类型：新增 Corpus Store 运行时依赖后的应用镜像验收。
- 结果：应用镜像重新安装 `psycopg[binary]`/`boto3` 后构建成功；统一 `tools/rag_infra.py up` 完成 app 与 MinIO 构建并启动，四个 Compose 服务均为 `healthy`。
- 安全边界：构建只使用 PyPI/Go module 镜像参数，不注入模型 API Key；PostgreSQL/MinIO 凭证仍只来自本地 `rag.env`，未写入镜像或 Git。

## 2026-07-21 — IDEA-CORPUS-INGEST-001

- 类型：Phase 2 Fetch 后耐久 Corpus 入库与 Run→Version 冻结绑定。
- 执行顺序：`NORMALIZE_AND_FETCH` 在保存成功检查点、进入文档分析和释放可重建全文之前，先把每篇 `FetchedDocument` 规范化写入 ObjectStore，再持久化不可变 Version 并建立 `(run_id, document_id)` write-once 绑定；Checkpoint 只记录 Version ID 列表和 `corpus_snapshot_hash`，不保存全文。
- 幂等与冲突：相同正文重试复用内容寻址对象和 Version；同一 Run/Document 已冻结到不同 Version 时 fail closed；缺少持久 Document ID、非 READY Version、对象缺失或哈希不一致时拒绝继续。
- PostgreSQL：新增 `PostgreSQLCorpusRunLinkRepository`，对 `run_document_versions` 执行 `ON CONFLICT DO NOTHING` 的冻结写入，并读取既有绑定进行确定性冲突核验。
- 运行时：仅当 `features.patent_corpus=true` 时装配 PostgreSQL Version/Run-Link Repository 和 S3ObjectStore；缺少 DSN、Endpoint、Bucket 或凭证时启动 fail closed，功能关闭时不要求外部存储。Compose 只从本地 `rag.env` 注入目标连接参数，不包含固定凭证。
- 运维：`tools/rag_infra.py up` 在服务健康后幂等确保 Corpus Bucket 存在；Bucket 名称和 Region 是非秘密环境配置，凭证仍由 Git 忽略且权限为 `0600` 的本地文件提供。
- 当前边界：功能开关继续默认关闭；现有 SQLite 运行时不会静默回退或双写一套新的 SQLite Corpus Schema，生产启用仍服从 PostgreSQL 业务数据迁移与核验边界。
- 涉及文件：`backend/idea/corpus.py`、`backend/idea/execution.py`、`backend/idea/postgres_corpus.py`、`backend/idea/runtime.py`、`deploy/rag/compose.yml`、`deploy/rag/rag.env.example`、`tools/rag_infra.py` 及对应测试。

## 2026-07-21 — IDEA-CORPUS-INGEST-001-VERIFY

- 类型：Corpus Ingest、Run-Version Freeze 和运行时门禁验证补记。
- 结果：Corpus/Execution/PostgreSQL/Runtime/RAG Infra 聚焦测试 34 项通过；完整离线套件 255 项通过；Python `compileall`、Compose `config --quiet`、Compose YAML 解析和 `git diff --check` 通过。
- Docker 环境：服务器安装 Docker Engine 29.6.2、Compose 5.3.1，并通过 SSH 反向代理真实拉取/运行 `hello-world`；Docker 系统代理与用户组配置位于服务器 `/etc`，不属于仓库，也不会进入 GitHub。
- 安全检查：新建 `deploy/rag/rag.env` 权限为 `0600` 且被 Git 忽略；测试、Compose 渲染和 Git diff 未包含模型 API Key、PostgreSQL/MinIO 密码或本地 Docker 代理地址。

## 2026-07-21 — IDEA-CORPUS-INGEST-001-RUNTIME-VERIFY

- 类型：Corpus Ingest 审查修正与真实 PostgreSQL/MinIO 运行态验收。
- 数据边界：新增 SQLite→PostgreSQL 前置身份桥接，先持久化 Case、Run、Input、Document 和 Run-Document 外键行，再写入 Corpus Version；桥接只复制专利元数据，`abstract_text`、`claims_text`、`description_text` 保持为空，耐久全文只进入内容寻址 Corpus Blob。
- 稳定身份：规范化正文排除 provider、URL 和 raw metadata，但保留确定性的 `section_spans`；来源和检索 metadata 进入独立的 `patent_version_sources`，没有真实原始响应时 `raw_response_hash` 保持为空。
- 深审状态：文档分析成功后，在同一 PostgreSQL 事务中同步更新 `run_document_versions` 和 `run_documents` 的 `deep_reviewed`；缺少 READY 绑定或同步数量不一致时 fail closed。
- 真实验收：Compose 应用、PostgreSQL、Redis、MinIO 四服务均为 `healthy`；随机隔离文档完成 SQLite 身份桥接、MinIO Blob 写入、PostgreSQL Version/Source/Run-Link 写入、幂等重试、Blob 读回和深审双表同步，随后按随机 Document/Publication 精确清理 PostgreSQL 与 S3 数据。
- 自动化验证：Corpus 聚焦测试 29 项通过（真实集成测试默认跳过）；完整离线套件 259 项通过、1 项按设计跳过；显式启用的真实 PostgreSQL/MinIO 集成测试 1 项通过；`git diff --check` 通过；最终代码复审结论为 READY。
- 环境与空间：云盘分区和 ext4 根文件系统已在线扩展到 40G，验收后可用 18G；本地 Docker 应用端口在 Git 忽略的 `rag.env` 中临时设为 `18001`，避免干扰既有 `127.0.0.1:8001` 开发进程。Docker 代理、端口和凭证设置均不进入 Git。

## 2026-07-21 — IDEA-CORPUS-CHUNK-001-POSTGRES-VERIFY

- 类型：结构化 Chunk 的 PostgreSQL 持久化与 Fetch 运行时接入。
- 执行顺序：Corpus Version 和 Blob 校验为 READY 后生成完整结构化 Chunk 集，Chunk 持久化成功后才建立 Run→Version 冻结绑定；Chunk 失败不会产生可供后续分析误用的新 Run Link，重试复用既有 Version/Blob。
- Repository：新增 `PostgreSQLPatentChunkRepository`，在单一事务内执行幂等插入，并按 `version_id + chunker_version` 回读完整集合；输入必须属于同一 Version、Chunker 版本和公开号，额外、缺失或内容冲突均 fail closed 并回滚。
- 运行时：`features.patent_corpus=true` 时同时装配 `PatentChunkPersistenceService` 和 PostgreSQL Chunk Repository；功能关闭时仍不要求外部基础设施。本切片不引入 FTS、embedding 或检索排序。
- 真实验收：随机隔离文档完成 Version、Source、Chunk 和 Run Link 写入及幂等重试；预置同一 Version/Chunker 的额外 stale Chunk 后，整批写入按预期拒绝，删除测试冲突行后流程恢复；测试结束精确清理 PostgreSQL 与 MinIO，退出钩子关闭全部 Compose 容器。
- 自动化验证：Chunk/Corpus/PostgreSQL/Runtime 聚焦测试 22 项通过；完整离线套件 262 项通过、1 项按设计跳过；显式真实 PostgreSQL/MinIO 集成测试 1 项通过；`compileall`、Compose `config --quiet`、`git diff --check` 和秘密扫描通过；最终代码复审结论为 READY。
- 空间边界：验收后根文件系统可用 18G，超过至少保留 5G 的门槛；保留可复用镜像和数据卷，未删除项目、数据库卷或 VS Code 数据。

## 2026-07-21 — IDEA-CORPUS-MIGRATE-001

- 类型：Phase 2 历史 Run 全文迁移与缺失正文重建入口。
- 安全默认：`tools/rehydrate_corpus.py` 默认 dry-run，只有显式 `--apply` 才允许抓取和 Corpus 写入；支持按 Run、数量上限筛选，并把不含专利正文的 JSON 审计报告以 `0600` 权限原子落盘。
- 迁移语义：只扫描 `COMPLETED`/`COMPLETED_WITH_LIMITATIONS` Run 中 `deep_reviewed=true` 的文档，普通候选继续只保留元数据；优先验证 SQLite 暂存正文 SHA-256 后迁移，正文缺失时标记 `REHYDRATABLE`，apply 模式通过既有 `RetrievalService`/`ProviderRunner` 复用 Provider 顺序、超时、限流、熔断、降级和 Tool Call 审计。
- 幂等与隔离：已存在 READY Run→Version 绑定时返回 `ALREADY_READY`；重建公开号与历史身份不一致、正文哈希不一致或绑定异常时 fail closed；单文档失败记录稳定错误码并继续处理后续文档，不把正文或异常中的已配置凭据写入报告。
- 运行时边界：迁移 CLI 只装配 Database、Cache、Provider 和 Corpus 适配器，不构建模型客户端、不恢复未完成工作流，也不调用大模型；公开 `build_corpus_ingest` 供维护工具复用，保留旧私有别名兼容既有调用。
- 涉及文件：`backend/idea/corpus_migration.py`、`backend/idea/retrieval.py`、`backend/idea/runtime.py`、`backend/idea/__init__.py`、`tools/rehydrate_corpus.py` 及对应测试。

## 2026-07-21 — IDEA-CORPUS-MIGRATE-001-VERIFY

- 类型：历史 Corpus 迁移真实运行态验收补记。
- 真实验收：随机隔离的终态历史 Run 完成 dry-run `LOCAL_READY`、首次 apply `MIGRATED_LOCAL`、重复 apply `ALREADY_READY`；PostgreSQL Version/Source/Chunk/Run-Link 与 MinIO Blob 写入后按随机身份精确清理。完整 Corpus 集成测试 2 项通过。
- CLI 冒烟：清空 PostgreSQL/S3 环境变量后，针对现有历史 SQLite 以 `--limit 1` 执行默认 dry-run，仍返回 `REHYDRATABLE`；未构造 Provider、Corpus、Cache 或外部连接，私有报告写入 `/tmp` 且不含正文或凭据。
- 自动化验证：完整离线套件 278 项通过、2 项按设计跳过；`compileall`、`git diff --check`、秘密模式扫描和定向安全测试通过。
- 空间边界：验收时根文件系统可用 18G，超过至少保留 5G 的门槛；测试完成后关闭 Compose 运行容器，保留可复用镜像和数据卷。

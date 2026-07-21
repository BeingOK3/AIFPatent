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

## 2026-07-21 — IDEA-RAG-PGFTS-001

- 类型：Phase 3 共享 PostgreSQL 词法检索、`pg_trgm` 与索引 repair。
- Schema：新增独立、幂等的 `030_lexical_schema.sql`，为 `patent_chunks` 增加应用层规范化 `search_terms`、生成式 `search_text`/`search_tsv`，并建立 GIN FTS 与 trigram 索引；`tools/rag_infra.py migrate` 按顺序执行 Corpus 与 Lexical 增量迁移，不删除或重建卷。
- 中英 tokenization：新增版本 `patent-lexical-v1`；NFKC/小写规范化保留英文术语、专利号、缩写、化学式与连接符标识符，中文连续文本同时写入完整词串和双字 token。新 Chunk 入库时原子写入 search terms 与 tokenizer 版本。
- 强制范围：所有查询必须携带非空且唯一的 `allowed_version_ids`，SQL 在召回阶段使用 `version_id = ANY(%s)` 过滤，并可追加章节范围；用户文本只作为参数进入 `plainto_tsquery`、精确子串和 trigram 路径，不执行任意 tsquery 表达式。
- 排名与审计：FTS rank、trigram similarity 和精确子串共同召回，按精确命中、词法分数、Chunk ID 确定性排序；返回 query ID、原始 rank、score、match kind 和完整 Chunk 身份，供后续 RRF/首次报告审计复用。
- Repair：按显式 Version 范围回读旧 Chunk，重建 search terms/tokenizer metadata，再验证范围内不存在空索引或版本不一致；失败回滚。
- 涉及文件：`backend/idea/lexical.py`、`backend/idea/postgres_lexical.py`、`backend/idea/postgres_corpus.py`、`deploy/rag/postgres-init/030_lexical_schema.sql`、`tools/rag_infra.py`、`backend/idea/__init__.py` 及对应测试。

## 2026-07-21 — IDEA-RAG-PGFTS-001-VERIFY

- 类型：PostgreSQL FTS/`pg_trgm` 真实运行态验收补记。
- 真实验收：现有 PostgreSQL 卷成功幂等执行 `020` 与 `030` 迁移；随机隔离 Corpus Version 的 Chunk 完成 tokenizer repair，并以显式 Version 范围召回真实 FTS 命中。长中英 Chunk 的中文 bigram query、英文拼写误差 word-similarity query 均命中，错误章节范围返回空；测试 Version、Chunk、Blob 与对象随后精确清理。
- 审查修正：trigram 候选改用 GIN 支持的 word-similarity 运算符，避免短查询与长 Chunk 的全串 similarity 稀释；中文 query 不再把 full run 与全部 bigram 强制 AND；repair 对缺失或混合 Version 范围精确比较并回滚。
- 自动化验证：Lexical/Schema/Infra 聚焦测试 21 项通过；完整离线套件 285 项通过、2 项按设计跳过；真实 PostgreSQL/MinIO Corpus 集成测试 2 项通过；`compileall`、`git diff --check` 和秘密模式扫描通过。
- 模型边界：本 Work Unit 不调用聊天模型或 embedding；后续 Hybrid Retriever 将以本接口输出进入 RRF，并在缺少 embedding 时记录 `LEXICAL_ONLY`。

## 2026-07-22 — IDEA-REPORT-RETRIEVAL-001

- 类型：首次报告 `F_i × D_j` 冻结词法检索编排。
- 范围门禁：`PostgreSQLReportScopeRepository` 只加载源 Run 中 `corpus_availability=READY`、`deep_reviewed=true` 且 Version 本身为 READY 的绑定；无可用 Version、重复 Version/Document、无 required Feature 均 fail closed。
- 检索矩阵：`InitialReportRetriever` 对每个 required Feature 与每个冻结 Version 发起独立 `allowed_version_ids=(version_id,)` 查询，验证返回命中的 query ID、Version、公开号和 rank，并检查完整笛卡尔积。
- 审计持久化：新增幂等 `035_report_retrieval_schema.sql` 和 `report_retrieval_queries`，即使某一对零命中也保留 query/hit_count；Chunk 命中继续写入既有 `report_retrieval_hits`。Feature 以 `<run_id>:<F_i>` 稳定主键同步到 PostgreSQL。
- 运维：`tools/rag_infra.py migrate` 追加执行 `035`，不删除表、卷或数据；本切片不调用模型 API，也不持久化模型凭证。
- 验证：领域编排、PostgreSQL adapter、Schema、FTS 与 Infra 聚焦测试 27 项通过；完整离线套件 291 项通过、2 项按设计跳过；现有 PostgreSQL 卷真实执行 `020/030/035` 幂等迁移成功，`035` 首次登记并创建检索审计表；容器随后停止且保留数据卷。
- 空间边界：验证后根文件系统可用 18G，高于至少保留 5G 的门槛。

## 2026-07-22 — IDEA-REPORT-CLAIMS-001

- 类型：首次报告结构化强制证据覆盖。
- 强制覆盖：对每个 required Feature 与冻结 Version，把全部摘要 Chunk 和全部 `claim_kind=independent` 的权利要求 Chunk 以 `forced_abstract`/`forced_claim` 原因加入检索审计；与 lexical 命中可共享 Chunk，但原因分别保留。
- 父链完整性：当 lexical 命中从属权利要求时，递归加入 `parent_claim_numbers` 指向的父权利要求；缺失父权利要求、父链循环、Chunk 越出冻结 Version 或公开号不一致均 fail closed。
- 降级语义：来源本身缺少摘要或可识别独立权利要求时，输出稳定 `MISSING_ABSTRACT`/`MISSING_INDEPENDENT_CLAIM` limitation，不伪造内容。
- Repository：`PostgreSQLPatentChunkRepository.list_for_versions` 只接受非空唯一 Version 范围，确定性排序回读完整 Chunk，并拒绝部分缺失范围。
- 验证：新增强制摘要、多个独立权利要求、递归父链和缺失父链测试；聚焦测试 15 项通过；完整离线套件 294 项通过、2 项按设计跳过。

## 2026-07-22 — IDEA-CONTEXT-CONTRACT-001

- 类型：首次报告确定性 Context Manifest 与 Citation binding。
- Context 身份：`ContextAssembler` 从完整 canonical manifest 计算 SHA-256，并生成稳定 `CTX-<hash-prefix>`；hash 覆盖消息、Prompt/Retriever 版本、Corpus snapshot、预算、选中/排除 Chunk 和 limitations。
- Citation binding：每个模型可见 `C#` 现在绑定 `chunk_id`、`version_id`、公开号、section type/label、claim number、start/end offset、text hash 和不可改写 excerpt。
- PostgreSQL：新增 `PostgreSQLContextRepository`，复用既有 `model_context_manifests` 表以 JSONB 持久化 Version allowlist、选中 Chunk、citation bindings、预算、omissions、完整 manifest 和 hash；相同 Context ID 的 hash 冲突 fail closed。
- 凭证边界：Context API 不接收 Runtime API Key，写入测试确认 manifest/SQL 参数不包含 `api_key` 或秘密值。
- 计划校正：既有 `010_core_schema.sql` 已包含完整 Manifest 表，因此不创建重复的 `040` Schema；后续只在运行时接入该 Repository。
- 验证：Context、adapter 和 PostgreSQL Context 聚焦测试 6 项通过；完整离线套件 295 项通过、2 项按设计跳过。

## 2026-07-22 — IDEA-REPORT-RAG-001

- 类型：LEXICAL_RAG 首次报告工作流接入。
- 状态顺序：保留 `deep_reviewed` 的真实含义；只有现有单文档分析全部成功后才同步 SQLite/PostgreSQL deep-review 状态，随后基于 Run 的冻结 READY Version 执行 `F_i × D_j` 检索并固化 Context，绝不预先伪造 deep-review 完成。
- Context 构造：`InitialReportRagService` 按文档聚合 forced/lexical 命中，按摘要、权利要求、词法命中稳定排序并按 Chunk ID 去重；每个文档生成一个 `INITIAL_REVIEW` Context Manifest。
- 工作流门禁：`initial_review_rag=true` 时缺少服务、Corpus snapshot hash、任一文档无可用证据或 Context 数量不完整均令 `ANALYZE_DOCUMENTS` fail closed；Checkpoint 只记录 Context ID 和 query 数，不存正文或凭证。
- 运行时：开启特性时装配 PostgreSQL scope/chunk/context Repository 与 lexical retriever；关闭时完全不要求外部 RAG 依赖。
- 验证：执行顺序、缺失服务、运行时装配、Context/检索聚焦测试 28 项通过；完整离线套件 298 项通过、2 项按设计跳过。

## 2026-07-22 — IDEA-REPORT-CITATION-001

- 类型：首次报告可验证 Citation 与 schema 2.0 输出。
- 验证器：`CitationVerifier` 只接受 `C1..Cn`，逐字段比较 Context binding 与真实 `patent_chunks` 的 Chunk/Version/公开号/section/claim/offset/hash/text，并重新计算 excerpt SHA-256；任一字段、正文或别名被篡改即 fail closed。
- PG 查询：`PostgreSQLCitationRepository` 从当前 Run 的 selected retrieval hit、INITIAL_REVIEW Context binding 和真实 Chunk 三方 JOIN，拒绝跨 Run feature，按 feature/context/chunk 去重后仅返回 `VerifiedCitation`。
- JSON：启用 Citation Repository 的报告升级为 `schema_version=2.0`，顶层保留 Citation index，并在 deep-review feature mappings 与 novelty matrices 按 Feature/公开号附加 Citation。
- Markdown：在深审文献的 Feature mapping 下输出 `依据：[C#] 公开号，章节` 和来自验证 Chunk 的原文 excerpt；Report Composer 仍无法创建或改写引用。
- 完成门：启用 RAG 时零 Verified Citation 不允许生成报告；manifest 记录 citation count/context IDs，不记录 BYOK。
- 验证：Citation 篡改、PG Run scope、JSON/Markdown 和兼容报告聚焦测试通过；完整离线套件 304 项通过、2 项按设计跳过。

## 2026-07-22 — IDEA-RUNTIME-DEFAULT-001

- 类型：LEXICAL_RAG 默认启用与一键 Docker 生命周期。
- 默认配置：仓库默认 `patent_corpus=true`、`initial_review_rag=true`、`followup_rag=false`；完整首次报告链路默认开启，追问 RAG 继续关闭。
- 启动：`./start.sh` 默认检查 Docker/Compose/Python/curl 和至少 5GiB 可用空间，幂等创建 mode 0600 的本地 `rag.env`，构建应用、启动四服务、确保 Bucket、执行增量迁移并等待 8001 健康；`--local` 保留旧纯本地兼容模式。
- 停止：`./stop.sh` 默认停止并移除 Compose 容器/网络，但不传 `--volumes`，PostgreSQL、Redis、MinIO 和应用数据卷全部保留。
- 网络故障修正：实际验收发现已缓存 MinIO 镜像仍被每次无条件重建，Docker Hub token 请求超时；根因修复为对象存储镜像存在时复用，仅缺失或设置 `AIFPATENT_REBUILD_OBJECT_STORE=1` 时重建。应用镜像仍随源码重建。
- BYOK：启动/停止脚本与 Compose 均不接收、导出或保存模型 API Key；网页仍按 Run 输入 Base URL、Model、API Key，刷新后消失。
- 真实验收：`./start.sh` 成功使 app/PostgreSQL/Redis/MinIO 全部 healthy，`127.0.0.1:8001/openapi.json` 可访问；`./stop.sh` 后 8001 不可访问、无容器残留且卷保留。
- 空间边界：验收后根文件系统可用 18G，高于至少保留 5G 门槛；脚本/配置/Infra 聚焦测试 21 项通过；兼容路径测试已显式关闭 RAG、不再依赖仓库默认值；完整离线套件 308 项通过、2 项按设计跳过。

## 2026-07-22 — IDEA-LEXICAL-RAG-E2E-001

- 类型：真实 DeepSeek 首次报告全链路与交付文档验收。
- E2E 工具：新增 `tools/e2e_lexical_rag.py`，只从临时环境变量读取 BYOK，创建真实 Case/Run，等待终态并校验 schema 2.0、Citation 字段、excerpt SHA-256 以及 deep-review mapping 引用；成功输出只含 Run ID 和计数，不打印报告正文或凭证。
- 模型兼容：DeepSeek `/models` 实际返回规范 ID `deepseek-v4-flash`，展示名大小写写法被 API 以 400 拒绝；README 已明确模型 ID 大小写敏感，通用运行时不擅自改写供应商 ID。
- 真实缺陷修正：首个到达报告阶段的 Run 被 Citation 完成门拦截，原因是 PostgreSQL `claim_number TEXT` 回读为字符串而领域 binding 为整数；修复位于 `PostgreSQLCitationRepository` adapter，将数据库值恢复为 `int | None` 后仍由严格 verifier 逐字段核验，不放宽安全条件，并新增真实 PG 类型回归测试。
- 真实结果：修复后 Run `b69e0cd9-705e-4c5d-be9a-2acd35c0991b` 完成全部 11 步并进入 `COMPLETED_WITH_LIMITATIONS`，生成 schema 2.0 报告；10 篇公开号、10 个 Context、78 个 Citation 全部通过 E2E hash/结构校验。
- 基础设施：真实 PostgreSQL/MinIO Corpus 集成测试 2 项通过；一键栈四服务 healthy。模型凭证仅经关闭终端回显的 stdin 进入临时进程，调用后 unset，未写入 `rag.env`、Git、报告或测试工具。
- 未完成范围：评审后追问聊天、Embedding/pgvector 检索、RRF、reranker、Citation 前端精细展开、Variant/法律状态增强和多租户认证继续留在后续阶段。
- 最终验证：报告 Markdown 包含 78 条 `依据：[C#]` 与 78 条 Chunk 原文，一一对应；完整离线套件 310 项通过、2 项按设计跳过；`compileall`、`git diff --check` 通过；仓库文本秘密模式扫描和应用容器环境检查均未发现模型凭证；测试栈已停止且 named volumes 保留；根文件系统可用 18G。待最终代码复审、提交推送和远端 SHA 对齐。

## 2026-07-22 — IDEA-LEXICAL-RAG-E2E-REVIEW-FIX-002

- 类型：首次报告 LEXICAL_RAG 语义纠正、模型实际 Citation 与独立数据库验收。本文明确取代 `IDEA-REPORT-RAG-001`、`IDEA-REPORT-CITATION-001` 和 `IDEA-LEXICAL-RAG-E2E-001` 中“旧分析完成后才建 Context”“全部 selected hit 作为 Citation”“报告自验 hash 即完成”的旧实现描述；历史记录保留用于说明发现过程。
- 正确顺序：`NORMALIZE_AND_FETCH` 冻结 Corpus snapshot/Version IDs 后，先在完全相同的 allowlist 上执行 `F_i × D_j` 词法检索和 mandatory evidence，再持久化每篇文献的 Context Manifest，然后才把该 Context 交给 DeepSeek 文档分析；所有分析成功后才同步 PostgreSQL deep-reviewed 状态。
- 模型实际引用：文档分析只接受当前 Context 中的 `C1..Cn`；`DISCLOSED/PARTIAL` 必须引用，`NOT_DISCLOSED/UNCERTAIN` 必须为空。新增 `report_model_citations` 只保存模型实际选择，并在重试时按 Run/Document/Context 同一事务先删旧选择再写新选择，避免失败尝试污染最终报告；全部负向披露时允许合法的零 Citation schema 2.0 报告。
- 强制证据与真实源缺陷：摘要和独立权利要求缺失、或预算排除任一 mandatory Chunk 时直接 fail closed。真实 E2E 发现 Google Patents 把 `CN120670335B` 的 1–9 项双语权利要求合并为单个 `claim-1`，后续文本引用 claim 6 导致旧 metadata 误标 dependent；修正只应用法律上严格成立的 `claim_number=1` 独立项不变量，并忽略该合并文本污染出的 later-parent，不放宽其他 claim 或摘要门禁。
- 审计与 provenance：retrieval hit 保留 `query_id`、`lexical_score`、`match_kind`；Citation 回读同时验证 Run scope、`corpus_availability=READY`、`deep_reviewed=true`、Version `state=READY`、Context binding 与 Chunk 全字段。报告及 Manifest 记录所有 Context IDs、Corpus snapshot、Prompt/Retriever/Context hashes 和 Citation text hashes。
- 独立 E2E：验收工具始终遍历报告 `rag_provenance.context_ids`，回查每个 PostgreSQL Context 及其 allowed Versions；非空 Citation 再逐条回查 Chunk 和 `report_model_citations`。因此即使合法零 Citation，也不能绕过 Version/Context 验证。
- 真实结果：最终 Run `a01f6ec9-17ea-4602-ab66-8c7bfb65dc25` 完成 11 步并进入 `COMPLETED_WITH_LIMITATIONS`；schema 2.0 报告的 20 条模型实际 Citation、10 个 Context、10 个冻结 Version 全部通过独立 PostgreSQL 验证，Citation 覆盖 6 个公开号。限制来自 Provider/来源范围，不是 RAG 或 Citation 验证失败。
- 安全：DeepSeek BYOK 仍只经关闭回显的 stdin 进入一次性进程环境，调用结束即 unset；没有写入 Git、`rag.env`、Context、报告、Manifest 或容器持久环境。规范模型 ID 为大小写敏感的 `deepseek-v4-flash`。
- 最终验证：完整离线套件 329 项通过、2 项按设计跳过；真实 PostgreSQL/MinIO 集成测试 2 项通过；`compileall`、`git diff --check` 通过。最终 Run 留存 40 个 Feature×Version query、140 个 retrieval hit（`query_id/lexical_score/match_kind` 缺失数为 0）、10 个 READY deep-reviewed Version、10 个 Context 和 20 个模型 Citation。三轮针对性代码复审最终均为 READY。
- 空间与生命周期：验收时根文件系统 40G 中可用 18G，高于至少保留 5G 的门槛；测试结束使用 `./stop.sh` 停止容器并保留 named volumes。
- 未完成范围不变：评审后追问聊天、Embedding/pgvector、RRF、reranker、Citation 前端精细展开、Variant/法律状态增强与多租户认证继续作为后续阶段，不伪装为本次完成项。

## 2026-07-22 — IDEA-EMBED-001

- 类型：Phase 4 部署级 Embedding Provider 与跨 Run 持久缓存基线。
- Provider：新增 `EmbeddingProvider` 协议与 OpenAI-compatible `/embeddings` 适配器；Provider、Model、维度和 L2 规范化共同生成稳定 `profile_id`，模型或维度变化会创建不同 Profile，不能覆盖旧向量。
- 缓存：按 Chunk 原文的 SHA-256 与 Profile 去重，批量请求仅发送缺失文本，结果校验数量、索引、维度、有限值和正范数后 L2 规范化；PostgreSQL 通过参数化 `vector` 写入和 `(text_hash, profile_id)` 唯一约束幂等复用。
- 凭证边界：Embedding 使用独立的部署环境变量 `EMBEDDING_API_KEY`；不读取网页 Run 的 Chat BYOK，不写配置、数据库、日志或响应。默认 `embedding.enabled=false`，未配置远程服务时现有 `LEXICAL_RAG` 行为不变。
- 配置：新增严格的部署级 `embedding` 配置段，默认预留本地 OpenAI-compatible BGE-M3 服务坐标，但不自动下载模型、不启动额外常驻服务，也不把专利正文发送到外部服务。
- 验证：配置、Provider、缓存、维度/非有限值/非规范化缓存门禁、凭证隔离和 PostgreSQL 参数化写入聚焦测试通过；完整离线套件 340 项通过、2 项按设计跳过，compileall 与 `git diff --check` 通过。真实 PostgreSQL Cache/关联验收将在 `IDEA-VECTOR-001` 与向量检索一起执行。
- 后续：`IDEA-VECTOR-001` 将补充 Chunk→Embedding 关联、严格 Version allowlist 的 pgvector 精确检索和真实数据库基准；此条不宣称混合 RAG 已启用。

## 2026-07-22 — IDEA-VECTOR-001

- 类型：Phase 4 `PgVectorIndex` 精确向量召回、Chunk 关联与小范围基准。
- 范围门禁：`VectorSearchRequest` 强制显式、非空且去重的 Version allowlist；参数化 SQL 在计算距离前以物化 CTE 同时限定 Version、ACTIVE Profile、维度和可选章节，adapter 回读后再次检查 Version/章节，任何越界结果 fail closed。
- 精确检索：当前关闭 index/bitmap scan 并使用 pgvector cosine distance，在单 Run 的少量深读 Version 内执行精确排序；返回 rank、distance 和 `index_mode=exact`，不把查询向量写入日志、Graph State 或 API。
- 索引生命周期：Embedding Service 校验 Chunk ID、正文 SHA-256 和唯一性后建立 Chunk→缓存向量关联；Profile 只有在要求的全部 Chunk 已关联时才能激活。激活事务锁定 Profile 表，并将旧 ACTIVE Profile 退役，避免多 Worker 并发产生多个 active profile。
- 基准：新增可复用的精确检索 benchmark 结果，记录请求数、命中数及 min/median/p95/max 延迟，不包含向量正文。
- 真实验收：在现有 PostgreSQL/pgvector Corpus 中随机选择两个不同 READY Version 的 Chunk，使用隔离的 3 维测试 Profile 完成首次生成、二次缓存复用、关联、激活、单 Version 防越界召回和 5 次精确检索基准；真实测试 1 项通过且 p95 小于 1000ms，随后按随机 Profile 精确删除测试关联和向量。
- 边界：默认 Embedding 仍关闭，未配置跨语言 Provider 时首次报告继续 `LEXICAL_RAG`；下一 Work Unit `IDEA-RAG-HYBRID-001` 才接入 RRF、章节权重和多样性。

## 2026-07-22 — IDEA-RAG-HYBRID-001

- 类型：Phase 4 共享混合 Retriever 排序内核。
- 融合：新增 `HybridRetriever`，词法 BM25 与 cosine 结果只按各自 rank 使用 Reciprocal Rank Fusion，默认 `k=60`；不直接相加不可比较的原始 lexical/vector score。Chunk 同时被两路召回时累加两个 rank 贡献，并保留 `lexical_rank`、`vector_rank`、来源与可解释最终分数。
- 章节权重：按 `CLAIM_OVERLAP`、`TECHNICAL_EXPLANATION`、`NOVELTY`、`DESIGN_AROUND` 和 `GENERAL` 问题类型应用显式权重；权利要求重合/新颖性优先独立权利要求，原理解释优先说明书，权重只改变排序，不创造证据。
- 多样性：融合后按精确 `text_hash` 去重，限制单 Version 和同一 section label 的 Chunk 数；比较型请求可在容量允许时先为每个有命中的指定 Version 保留一个证据，避免 Top-K 被单篇专利或超长权利要求滑窗垄断。
- 安全与降级：词法和向量请求使用完全相同的冻结 Version/章节 allowlist，两个 adapter 的回读结果再次验 scope；Chunk ID 对应正文冲突、query ID/rank 异常或范围越界均 fail closed。缺少向量坐标时明确返回 `LEXICAL_ONLY` limitation，绝不伪装成混合召回。
- 配置：新增严格 `rag.hybrid` 配置，版本化保存 RRF k、两路候选上限、最终上限和多样性配额；默认参数为 60/40/40/12/4/2。
- 验证：RRF 双路增益、章节权重、跨 Version 配额、正文哈希去重、section/version 上限、词法降级、冲突与范围越界门禁及配置边界聚焦测试通过。完整回归在本 Work Unit 提交前执行。
- 后续：当前为共享排序内核；首次报告切换与追问 Workflow 仍需分别接入同一 `HybridRetriever`，并在真实跨语言 embedding 评测通过后才能默认开启。

## 2026-07-22 — IDEA-FOLLOWUP-DB-001-SCHEMA

- 类型：Phase 4 追问 PostgreSQL Schema 与状态机保存点。
- 数据表：新增 `followup_threads`、`followup_turns`、`followup_retrieval_hits` 和 `followup_citations`；Thread 绑定源 Run 与冻结 Corpus snapshot，Turn 保存问题哈希、模式、范围、计划/回答、模型和检索版本，命中绑定真实 Chunk，Citation 必须引用本 Turn 已检索的 Chunk。
- 范围/不可变性：Thread 的源 Run、scope 和 snapshot 创建后不可修改；Turn 的 parent、问题、mode、scope、模型/Prompt/Retriever 和 snapshot 从入队起冻结；终态 Turn 任意更新均拒绝。数据库 trigger 只允许 `QUEUED→RUNNING/FAILED/CANCELLED` 与 `RUNNING→终态`。
- 完成门：COMPLETED 状态必须同时具有 answer 与完成时间，FAILED 必须具有 error code 与完成时间；retrieval hit 至少有 lexical/vector rank 之一，同一 Turn final rank 唯一；Citation 以 `(turn_id, chunk_id)` 外键保证不能引用未检索证据。
- Context 关联：现有 `model_context_manifests.turn_id` 增加到追问 Turn 的外键，首次报告 Context 的 NULL turn 不受影响；删除源 Run 时按既有运维语义级联 Thread/Turn，但专利 Chunk 继续 RESTRICT 保护耐久证据。
- 迁移：新增幂等 `050_followup_schema.sql` 并加入 `tools/rag_infra.py migrate`；现有 PostgreSQL 数据卷已成功执行，旧表/报告未修改，第二次执行将继续由 IF NOT EXISTS/迁移记录保护。
- 验证：Schema 与基础设施 19 项聚焦测试通过，真实 PostgreSQL 迁移完整提交；此保存点只建立数据契约，尚未开放追问 API 或启用 `followup_rag`。

## 2026-07-22 — IDEA-FOLLOWUP-DB-001-REPOSITORY

- 类型：Phase 4 追问冻结范围、Thread/Turn 生命周期与 Retrieval/Citation 仓储。
- Scope：Thread 只能从 PostgreSQL 中 `COMPLETED`/`COMPLETED_WITH_LIMITATIONS` 的源 Run 创建，并只冻结该 Run 的 `deep_reviewed=true`、Corpus/Version `READY` 文档；用户选择的公开号必须是该集合子集。Scope 记录 Document/Version/公开号/正文哈希并重算与 Corpus 相同的 snapshot hash，任何存储篡改在 adapter 回读时拒绝。
- Thread/Turn：公开共享仓储提供创建/读取/列表/归档 Thread，以及创建、启动、保存计划、完成、失败和取消 Turn；父 Turn 必须属于同一 Thread 且已完成，归档 Thread 不接收新 Turn。Repository 方法不接收 API Key/Authorization，Turn 只保存模型名和版本化 Prompt/Retriever 坐标。
- 检索审计：仅 RUNNING Turn 可写检索；`HybridSearchResult.retriever_version` 必须与 Turn 相同，所有 Chunk 必须真实存在且位于冻结 Version scope。允许模型调用前的受控重试替换命中，但已有 Citation 后禁止替换；final rank、双路 rank、RRF、query source/mode 均持久化，不保存查询向量。
- Citation：只允许引用本 Turn `selected_for_context=true` 的 Chunk；公开号/章节从数据库复制，quote 必须与 Chunk 正文及精确 offset 一致，quote hash 与 citation ID 由后端确定性生成。伪造正文、偏移、未检索 Chunk 或跨 Version 引用均 fail closed。
- 实际缺陷修正：真实验收发现 PostgreSQL 桥接的 5 个已有 Run 全部停留在 Corpus ingest 时的 `RUNNING`，导致合法历史报告无法创建 Thread。新增工作流 `finally` 终态同步，只更新 PostgreSQL Run 的 status/limitation/timestamps/error 等可变字段，不触碰不可变输入；新增 `tools/sync_postgres_run_status.py` 修复历史桥接。
- 远程数据修复：维护工具从应用 SQLite 卷读取 6 个终态 Run，其中 5 个存在 PostgreSQL 桥接并成功同步；修复后 PostgreSQL 为 3 个 `COMPLETED_WITH_LIMITATIONS`、2 个 `FAILED`，未桥接的 1 个 Run 保持只在源库，不伪造记录。
- 真实验收：使用随机 Case/Run/Thread 在真实数据库复用一个 READY Corpus Version，完成 Thread、限定单文献 Turn、计划、Hybrid retrieval、原文 Citation、`COMPLETED_WITH_LIMITATIONS`、终态篡改拒绝、子 Turn 失败与 Thread 归档；随后精确删除随机 Case 及级联测试记录。聚焦 28 项与真实集成 1 项通过。
- 未完成：尚未实现固定追问 Workflow、回答 schema/引用完成门、API/SSE 和 UI；`followup_rag` 继续默认关闭。

## 2026-07-22 — IDEA-FOLLOWUP-WF-001

- 类型：Phase 4 独立固定追问 LangGraph Workflow 骨架。
- 固定图：严格按 `PREPARE_FOLLOWUP_SCOPE → CLASSIFY_AND_PLAN → RETRIEVE_FOLLOWUP_EVIDENCE → ASSEMBLE_FOLLOWUP_CONTEXT → GENERATE_FOLLOWUP_ANSWER → VERIFY_FOLLOWUP_ANSWER → PERSIST_FOLLOWUP_RESPONSE` 七节点执行，模型或 Handler 不能选择、跳过或新增控制流节点。
- Checkpoint：`FollowupGraphState` 仅允许 `turn_id/thread_id/run_id/last_completed_step/completed_steps`；问题、历史对话、专利正文、Context、向量、模型输出和 API Key 不进入 LangGraph SQLite Checkpoint。
- 生命周期：QUEUED Turn 执行前原子切到 RUNNING；已终态 Turn 幂等返回且不重跑 Handler；任一节点异常只令当前 Turn FAILED，任务取消令当前 RUNNING Turn CANCELLED，不修改原 Run、报告或其他 Turn。
- 完成门：最后节点必须通过仓储持久化 `COMPLETED` 或 `COMPLETED_WITH_LIMITATIONS`；图正常结束但 Turn 仍为 RUNNING 时强制失败为 `PERSIST_FOLLOWUP_RESPONSE_INCOMPLETE`，禁止伪成功。
- 资源：节点有独立超时与 LangGraph RetryPolicy，进程内 attempt 计数在 Turn 终态清理；持久 Checkpoint 保留最小进度，服务重启后的临时 BYOK 丢失语义将在 API/Job Work Unit 接入。
- 验证：固定顺序、最小 State 契约、单节点失败隔离、终态幂等、缺失 Turn、最终持久化门禁和 asyncio 取消聚焦测试通过；此保存点仍使用抽象 Step Handler，尚未接入模型回答。

## 2026-07-22 — IDEA-FOLLOWUP-ANSWER-001

- 类型：Phase 4 追问结构化回答契约与 Citation 完成门。
- 回答契约：新增严格 Pydantic schema，区分直接回答、技术重合分析、规避设计、证据不足和需要新检索；重合项、差异、工程取舍、剩余风险与法律边界均为独立字段，未知字段 fail closed。
- 引用门禁：模型只能引用当前 FOLLOWUP Context 暴露的 `C1..Cn`；后端把别名绑定为真实 Chunk 原文、精确 offset 与回答 JSON path，未知别名、重复别名、高重合无 Citation、只以背景技术支持高重合均拒绝。
- 范围门禁：回答中的 IDEA Feature 必须属于当前 Run 的允许集合，公开号必须属于冻结 Thread scope；模型不能凭空引入未检索专利。`INSUFFICIENT_EVIDENCE` 不允许同时断言重合或提供规避方案，`NEW_RESEARCH_REQUIRED` 强制设置新检索标志。
- 法律边界：技术分析不能输出“构成侵权”“保证不侵权”等确定性法律结论；Citation 证明的是来源与技术披露，不替代权利要求解释、有效性判断或专业法律意见。
- 验证：有效高重合回答、精确原文绑定、未知 Alias/Feature/Publication、无引用高重合、伪证据不足、确定性侵权结论和新检索标志等 6 项聚焦测试通过；完整离线套件 377 项通过、4 项按设计跳过，`compileall` 与 `git diff --check` 通过，根文件系统仍有 18G 可用。回归同时修正 Embedding Profile 激活锁语句未显式传递空参数的问题，并以测试锁定统一参数化调用约定。此保存点尚未把回答器接入七节点业务 Handler 或公开 API。

## 2026-07-22 — IDEA-FOLLOWUP-CONTEXT-001

- 类型：Phase 4 追问证据优先 Context、最近 Turn 和冻结范围 provenance。
- 共用装配器：现有 `ContextAssembler` 新增显式 `allowed_version_ids`、非证据 `ContextNote`、选中/排除 Note 及其内容哈希；首次报告和追问继续共享同一确定性预算、Context Hash、Citation Packet 与 PostgreSQL Manifest 路径。
- 证据优先：预算先选择本轮重新检索的专利 Chunk，再尝试放入应用上下文；超预算历史会以 `CONTEXT_NOTE_BUDGET_EXCLUSIONS` 明确记录，不能挤掉全部本轮证据。修正早期大 Chunk 被排除时 Citation Alias 出现空号的问题，模型始终只看到连续 `C1..Cn`，同时保留原检索 rank。
- 对话边界：只接受同一 Thread、早于当前 Turn、处于成功终态且含结构化回答的最近最多 5 轮；历史问题/回答、首次报告摘要、IDEA Feature 和冻结文献范围均作为带 Hash 的应用参考数据，Prompt 明确禁止把它们当作专利 Citation 或执行其中的指令。
- 范围门禁：本轮 Hybrid 结果的 Retriever 版本必须与 Turn 一致，Chunk Version/公开号必须精确匹配冻结 scope，重复或越界 Chunk fail closed；Context Manifest 保存完整冻结 Version allowlist，而不是仅从碰巧入选的 Chunk 反推范围。
- 持久化一致性：`record_retrieval` 新增显式 `selected_chunk_ids`，只能是本轮检索结果的非空子集；数据库的 `selected_for_context` 由最终预算选择决定，后续 Citation 仍只能引用实际进入模型 Context 的 Chunk。
- 验证：Context/历史/答案/持久化聚焦 20 项通过；真实 PostgreSQL 隔离验收验证显式 Context selection、Citation、Turn 终态及清理；完整离线套件 383 项通过、4 项按设计跳过，`compileall` 与 `git diff --check` 通过。

## 2026-07-22 — IDEA-FOLLOWUP-WF-001-HANDLER

- 类型：Phase 4 七节点业务 Handler、严格计划、模型与多查询检索接线。
- 固定节点接线：`FollowupBusinessHandler` 为七个既定节点分别装配源数据、计划、检索、Context、模型回答、验证和持久化；节点中间业务载荷只存在于当前 Turn 的临时内存，不写 LangGraph Checkpoint，终态/失败/取消后由 Workflow 主动清理。
- 计划契约：新增严格 `FollowupPlan`，校验冻结 Turn mode、公开号子集、已知 Feature、章节枚举、去重 query rewrite 及 NEW_RESEARCH/设计规避一致性；Agent 不能借计划扩大文献或 Feature 范围。
- 数据源：`PostgreSQLFollowupDataSource` 从源 Run 加载耐久 IDEA Feature、Run 限制、Novelty/Value 摘要以及同 Thread 最近最多 5 个成功回答；Turn→Thread→Run 身份、Feature 前缀和 source span 均 fail closed。
- 多查询检索：每个已验证 rewrite 使用完全相同的计划文献 Version allowlist 和章节过滤调用共享 `HybridRetriever`；跨查询按 Chunk 合并 RRF 贡献、正文哈希去重并再次应用全局文献/section 多样性配额，聚合 query ID 稳定可审计。Embedding 未提供时明确保留 `LEXICAL_ONLY`。
- 模型：现有 `StructuredModelClient` 注册 follow-up planner/answerer 严格 Schema，继续复用网页瞬时 BYOK、OpenAI-compatible Base URL、结构化重试和中文输出门禁；请求只含问题、范围、Feature、历史、计划与已装配 Context，不接收或持久化 API Key 参数。
- 原子完成顺序：先记录仅实际进入 Context 的 Retrieval selection 与 Context Manifest，再调用模型；回答通过 Alias/Feature/Publication/法律边界校验后，先写永久 Citation，最后才把 Turn 置为成功终态。任一中间失败不生成伪成功回答。
- 验证：计划、Retriever、模型适配器、Handler、固定 Workflow 聚焦测试通过；真实 PostgreSQL 隔离验收覆盖 Feature、源 Run 摘要、父 Turn 历史、Retrieval/Citation/终态与精确清理；完整离线套件 393 项通过、4 项按设计跳过，`compileall`、`git diff --check` 通过，根文件系统仍有 18G 可用。
- 未完成：Web API、后台任务/BYOK 生命周期、SSE 事件与前端 Thread UI 尚未开放；因此本保存点不宣称用户已能从网页发起追问。

## 2026-07-22 — IDEA-FOLLOWUP-API-001

- 类型：Phase 4 追问 HTTP API、SSE、取消、Citation 回读与瞬时 BYOK 生命周期。
- API：新增可追问文献列表、创建/列出/读取/归档 Thread、创建/读取/取消 Turn、Turn SSE 事件及永久 Citation 详情接口；所有输出包含冻结 scope、计划、状态、限制、结构化回答和可展开原文定位，但不返回凭证或向量。
- 作业生命周期：`FollowupTaskManager` 每个 Turn 只在进程内保存 `RuntimeModelConfig`，通过既有 ContextVar 把网页提供的 Base URL/Model/API Key 绑定到当前异步任务；终态、异常、取消和 shutdown 均清除任务与凭证。服务重启后 QUEUED/RUNNING Turn 统一进入 `RUNTIME_API_KEY_REQUIRED_AFTER_RESTART`，不会无凭证续跑。
- SSE/取消：事件流只在 Turn 视图变化时推送 progress，终态再发送 terminal 并结束；取消正在执行的任务会触发 Workflow 的 Turn 隔离取消语义，不修改首次报告或其他 Turn。
- Runtime 门禁：`build_runtime` 仅在 `features.followup_rag=true` 时装配 PostgreSQL Repository/DataSource、共享 HybridRetriever、结构化模型、业务 Handler、独立 Checkpoint 和 API Manager；默认配置仍为 false，因此当前保存点不改变既有首次报告部署行为。
- 数据回读：Repository 新增按 Thread 排序的 Turn、按 Turn 的 Citation、单 Citation 和源 Run 可追问文献查询；真实 PostgreSQL 隔离验收验证回读身份与精确清理。
- 验证：API/BYOK/SSE/取消聚焦测试及既有 API 回归通过；真实 PostgreSQL 集成通过；完整离线套件 397 项通过、4 项按设计跳过，`compileall` 与 `git diff --check` 通过。
- 未完成：前端 Thread/Turn/Citation 交互尚未实现，功能门尚未默认打开；启用前还需完成 UI 与一次真实 DeepSeek 端到端追问验收。

## 2026-07-22 — IDEA-FOLLOWUP-UI-001

- 类型：Phase 4 报告内追问工作台、SSE 状态与 Citation 原文展开。
- 页面流程：成功首次报告下方显示可追问深读文献；用户选择冻结范围并创建 Thread，可切换已有 Thread，以证据问答、规避设计或新检索模式连续创建 Turn。父 Turn 自动绑定最近成功回答，失败 Turn 不污染后续历史。
- BYOK：追问复用页面顶部 Base URL、Model 和 API Key 三项输入，通过 `runtimeModelPayload()` 只随创建 Turn 的 POST 发送；刷新/关闭/新建工作区仍清空，不使用 localStorage/sessionStorage，也不把密钥渲染回页面。
- 实时状态：每轮通过独立 SSE 显示 QUEUED/RUNNING/终态并支持取消；重新选择历史 Thread 可从 PostgreSQL 恢复问题、计划、结构化回答、限制和 Citation，无需依赖浏览器内存。
- 回答展示：直接回答、逐 Feature 重合、差异、规避候选和法律边界分层显示；永久 Citation 以 `<details>` 展开公开号、章节、回答路径和精确 Chunk 原文，所有外部数据使用 `textContent` 构造，避免 HTML 注入。
- 默认门禁：`features.followup_rag` 已切换为 true，`./start.sh` 的启动提示同步为“首次报告与证据追问 RAG”；完整 UI/后端仍依赖已有 PostgreSQL Corpus 与首次报告完成门。
- 验证：工作区 Node.js `--check` 通过；前端 DOM/API/BYOK/Citation 契约、配置和容器聚焦 23 项通过；完整离线套件 398 项通过、4 项按设计跳过，`compileall`、`git diff --check` 通过，根文件系统仍有 18G 可用。
- 待验收：尚需重建运行镜像，并以真实已有报告 + DeepSeek 瞬时 BYOK 完成一次 Thread→Turn→Hybrid/词法降级→Context→Answer→Citation→SSE 全链路验收；完成前不把本条当作运行态 E2E 结论。

## 2026-07-22 — IDEA-FOLLOWUP-E2E-FIX-001

- 类型：首次真实 DeepSeek 追问验收发现的零召回修正。
- 失败留痕：真实 Thread `FT-02a364c2cdcd4237983f23e92975303d` 的首个 Turn 成功完成计划模型调用，但 5 条包含公开号、独立权利要求和多个技术词的长查询在 `claims` 范围内均零命中，Turn 按设计进入 FAILED，未生成回答或 Citation；凭证未出现在日志或持久记录。
- 根因：PostgreSQL 词法路径使用受控 `plainto_tsquery`，长 query 的 token 组合过严；追问编排虽然防止越界，却没有在“计划 query 全部零命中”时执行按文献的强制最低证据补齐。
- 修正：完成所有模型 query 后，逐个检查计划中的冻结 Version；没有任何命中的 Version 使用“该 Version 自身公开号”作为参数化 seed query，先在计划章节（无章节时默认 claims）取最多 2 个 Chunk，仍无结果才放宽章节但不放宽 Version。命中标记 `MANDATORY_VERSION_EVIDENCE_FALLBACK`，章节放宽另记 `SECTION_FILTER_FALLBACK`，不能伪装为语义相关命中。
- 安全：seed query 只来自已验证公开号，每次只允许单个冻结 Version；返回后继续执行 Version scope、正文哈希去重、全局多样性和 Context/Citation 门禁。它只保证比较型问题至少读到每篇指定文献的耐久证据，不把未命中推断为重合。
- 验证：零命中长查询→单 Version claims seed→可审计证据的聚焦测试新增并通过；追问 Handler/Hybrid 聚焦 12 项通过；完整离线套件 399 项通过、4 项按设计跳过，`compileall` 与 `git diff --check` 通过。待重建镜像后以同一 Thread 新 Turn 复验。

## 2026-07-22 — IDEA-FOLLOWUP-E2E-FIX-002

- 类型：第二轮真实 DeepSeek 追问验收发现的法律免责声明误报修正。
- 失败留痕：修复零召回后，新 Turn 已完成计划、按 Version 证据补齐、Context 和回答模型调用，但模型在必填 `legal_boundary` 中写明“不能判断构成侵权或不构成侵权”；旧扫描器对整份 JSON 做禁语子串匹配，把免责声明误判为确定性法律结论，Turn 按 fail-closed 进入 FAILED，未落回答或 Citation。
- 修正：确定性法律结论扫描只检查可能承载业务断言的直接回答、重合项、差异和规避候选；专门用于声明边界的 `legal_boundary` 与诚实限制 `limitations` 不参与禁语命中。直接回答或分析字段输出“构成侵权”“保证不侵权”等结论仍严格拒绝。
- 验证：新增“免责声明可明确提及被禁止结论”的正向测试，原两项确定性侵权/不侵权负向测试继续通过；答案/Handler 聚焦 10 项通过；完整离线套件 400 项通过、4 项按设计跳过，`git diff --check` 通过。待重建镜像后第三轮复验。

## 2026-07-22 — IDEA-FOLLOWUP-E2E-001

- 类型：报告内证据追问 MVP 的真实 DeepSeek 端到端运行态验收。
- 运行链路：在已完成首次报告 `a01f6ec9-17ea-4602-ab66-8c7bfb65dc25` 上创建 Thread `FT-02a364c2cdcd4237983f23e92975303d`，冻结 `CN101236530A` 与 `CN102760101A` 两个 READY Version；第三轮 Turn `FU-67ac89e82f594112a9874baab2500a6e` 完整通过计划、范围受限检索、Context 装配、结构化回答、回答验证、Citation 落库和终态持久化。
- 结果：Turn 终态为 `COMPLETED_WITH_LIMITATIONS`，回答类型为 `OVERLAP_ANALYSIS`；限制明确包含 `LEXICAL_ONLY` 与 `MANDATORY_VERSION_EVIDENCE_FALLBACK`，没有把词法补齐伪装成语义匹配。模型输出 1 个重合项、3 个差异项，并对证据不足作出显式说明。
- 独立回查：PostgreSQL 中存在 4 个 Retrieval Hit，全部进入本轮 Context；存在 1 份 FOLLOWUP Context Manifest、2 个 allowed Version、4 个 selected Chunk 和 2 条永久 Citation。Citation 分别绑定 `CN102760101A` 的 `claim-4` 与 `claim-11`，quote hash、answer path、Chunk 外键及冻结 snapshot 均由数据库事实层校验。
- 缺陷留痕：同一 Thread 中保留前两轮 FAILED Turn，分别记录长词法 query 零召回与法律免责声明误报；它们未生成伪成功回答或 Citation，修正见 `IDEA-FOLLOWUP-E2E-FIX-001/002`。
- 凭证边界：真实模型凭证仅随 Turn 请求进入进程内存，提交后立即从测试 shell 变量移除；凭证未进入仓库、配置、日志、Turn、Checkpoint 或 Context Manifest。
- 完成结论：Phase 4 的报告内追问 MVP 已达到“用户从已完成报告创建 Thread 并获得带可验证 Citation 的结构化回答”的可用门槛。后续重点转向首次报告默认 Hybrid 切换、reranker 与统一 RAG 评测集，不再把追问 API/UI/E2E 列为未完成。

## 2026-07-22 — IDEA-RAG-EVAL-001-CONTRACT

- 类型：Phase 4 首次报告/追问共用的离线 RAG 评测契约、指标与 PostgreSQL 词法采集基线。
- Ground truth：新增严格 `rag-eval-dataset-v1`，每个 Case 固定 `INITIAL_REPORT`/`FOLLOWUP`、问题、允许 Version、相关 Chunk 的 1～3 级相关性、比较型问题必须覆盖的 Version，以及精确 Citation quote hash；相关 Chunk 或 Citation target 越出冻结范围、身份重复或 hash 非法时拒绝加载。
- 观测与指标：`rag-eval-run-v1` 记录待测系统实际返回的 Chunk/rank/source 与 Citation，不保存正文、查询向量、DSN 或凭证。评测输出 Precision@K、Recall@K、MRR@K、nDCG@K、Version coverage、Citation precision/recall，并把范围越界、引用未检索 Chunk、引用不匹配 ground truth 作为可配置的硬门禁。
- 回归比较：`rag-eval-summary-v1` 可作为 baseline；candidate 必须满足显式 recall/nDCG/Citation delta，且不能新增范围或 Citation 违规。CLI 使用不同退出码区分质量门失败和候选回归，适合后续 CI/发布门禁。
- 真实数据入口：`collect-postgres-lexical` 只从环境读取 DSN，对数据集中每个 Case 以参数化查询和原冻结 Version allowlist 采集 PostgreSQL 词法结果；collector 对任何 adapter 越界结果 fail closed。当前服务器已连接真实 Corpus 完成采集器冒烟，输出不含专利正文或连接信息。
- 验证：指标精确值、双 Workflow 覆盖、数据契约、Case 覆盖、质量门、回归比较、越界/伪 Citation 和 collector fail-closed 共 8 项聚焦测试通过；合成 baseline→candidate 示例在 Recall@2 提升 `0.25`、nDCG@2 提升约 `0.1064` 且零新增违规时通过比较。
- 边界：仓库中的 `example-*.json` 仅验证契约与工具，不是产品质量结论。首次报告切换 Hybrid 前仍需建立来自真实授权 Corpus、经人工复核且不泄露用户 IDEA/全文的私有评测集，并记录 lexical baseline 与 candidate 实测结果。

## 2026-07-22 — IDEA-REPORT-HYBRID-001

- 类型：Phase 4 首次报告默认接入与追问共用的 `HybridRetriever`。
- 接线：`InitialReportRetriever` 保留既有严格 `Feature × Version` 矩阵、单 Version 查询范围、摘要/全部独立权利要求强制证据和父权利要求链；默认运行路径改为共享 RRF、问题类型章节权重、正文 hash 去重和多样性内核。旧词法 adapter 仍作为显式兼容注入路径用于历史测试，不再是生产 runtime 默认。
- 统一命中契约：新增 `ReportEvidenceHit`，统一保存 final/lexical/vector rank、词法分数、RRF、章节权重、最终分数和来源；Context 仍只消费受验证的真实 Chunk，不把排序元数据变成证据。每个 Feature 的 query ID 纳入 Retriever 版本，避免不同算法的审计坐标冲突。
- 审计迁移：新增幂等 `055_report_hybrid_schema.sql`，扩展首次报告 Retrieval Hit 的 `hybrid` 原因、final rank、section weight、final score 和 query sources；迁移已在当前真实 PostgreSQL 数据卷执行并回查成功，旧报告和 Citation 未修改。
- 降级语义：当前部署级 Embedding 默认关闭，因此首次报告和追问都运行共享 Hybrid 内核的词法分支并明确记录 `LEXICAL_ONLY`；不得以 `hybrid-rrf-v1` 版本名掩盖实际没有向量命中。Embedding query adapter 和 Chunk Profile 自动索引/激活仍是下一保存点。
- 真实验收：隔离集成测试从现有 Corpus 选择同时具备摘要和独立权利要求的 READY Version，创建随机 Case/Run，执行真实 PostgreSQL 词法→Hybrid fallback→强制证据→审计落库，验证 RRF/final/source 字段和零 vector rank，最后级联清理随机业务记录并保留共享 Corpus。
- 验证：Hybrid 接线、旧兼容路径、强制证据、Context 预算、Repository、runtime、迁移与基础设施聚焦 40 项通过；真实 PostgreSQL 集成 1 项通过；完整离线套件 411 项通过、5 项按设计跳过。

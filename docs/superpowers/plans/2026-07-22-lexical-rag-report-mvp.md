# LEXICAL_RAG 首次报告 MVP 实施计划

> **执行约束：** 严格按测试驱动方式逐项实施。每个任务完成相关测试、更新 `development/followup-rag/development-log.md`、提交并推送 `origin/develop` 后，才能开始下一任务。任何模型密钥只允许存在于前端请求和运行进程内存中。

**目标：** 让一台新机器克隆 `develop` 后通过 `./start.sh` 启动完整运行栈，并生成包含可验证 Chunk Citation 的首次评审 JSON/Markdown 报告。

**架构：** 保留现有固定 11 步 IDEA 工作流和 SQLite 业务结果，PostgreSQL 作为不可变 Corpus、Chunk、检索命中、上下文清单和 Citation 绑定的事实层。开启 `initial_review_rag` 时，文档分析证据包从源 Run 的 `READY + deep_reviewed` Version 范围中构造；旧非 RAG 路径仅作为关闭特性时的兼容实现。

**技术栈：** Python 3.12、FastAPI、Pydantic、psycopg 3、PostgreSQL 17/pgvector、MinIO、Redis、Docker Compose、`unittest`。

---

## Task 1：冻结首次报告检索范围并审计 `F_i × D_j`

**文件：**

- 新建：`backend/idea/report_retrieval.py`
- 修改：`backend/idea/postgres_corpus.py`
- 修改：`backend/idea/postgres_lexical.py`
- 修改：`deploy/rag/postgres-init/020_corpus_schema.sql`
- 测试：`backend/tests/test_report_retrieval.py`
- 测试：`backend/tests/test_corpus_integration.py`

**步骤：**

1. 先写测试，要求检索只能使用指定 Run 中 `corpus_availability='READY' AND deep_reviewed=true` 的 Version。
2. 写测试，要求每个 required Feature 与每个 Version 都产生一次检索审计；缺任一 `(feature_id, version_id)` 时失败关闭。
3. 写测试，要求 PostgreSQL 前置同步包含 `idea_features`，且数据库持久化的 feature 主键为 `<run_id>:<F_i>`。
4. 实现 `PostgreSQLReportScopeRepository`，加载冻结 Version 范围、同步 Feature、持久化/读取 `report_retrieval_hits`。
5. 实现 `InitialReportRetriever`，为每个 `F_i × D_j` 构造 Version-scoped `LexicalSearchRequest`，去重并保持稳定排序。
6. 运行：`cd backend && .venv/bin/python -m unittest tests.test_report_retrieval tests.test_lexical_search tests.test_corpus_integration -v`。
7. 记录、提交并推送：`feat: orchestrate scoped report retrieval`。

## Task 2：强制摘要、独立权利要求及父权利要求链覆盖

**文件：**

- 修改：`backend/idea/report_retrieval.py`
- 修改：`backend/idea/postgres_corpus.py`
- 测试：`backend/tests/test_report_retrieval.py`
- 测试：`backend/tests/test_chunks.py`

**步骤：**

1. 先写测试，要求每个 Version 至少选择摘要 Chunk 和全部独立权利要求 Chunk。
2. 写测试，要求选中从属权利要求时递归包含其 `parent_claims`，并检测缺失/循环父链。
3. 实现按 `selection_reason` 合并 forced 与 lexical 命中；同一 Chunk 保留可审计原因但上下文只出现一次。
4. 实现完整性门：摘要或独立权利要求不存在时返回结构化 limitation；父链不完整时失败关闭。
5. 运行相关单测，更新日志，提交并推送：`feat: force core patent evidence coverage`。

## Task 3：持久化确定性 Context Manifest

**文件：**

- 修改：`backend/idea/context.py`
- 新建：`backend/idea/postgres_context.py`
- 新建：`deploy/rag/postgres-init/040_report_rag_schema.sql`
- 修改：`tools/rag_infra.py`
- 测试：`backend/tests/test_context.py`
- 新建测试：`backend/tests/test_postgres_context.py`
- 修改测试：`backend/tests/test_postgres_schema.py`

**步骤：**

1. 先写测试，要求别名稳定绑定 `chunk_id/version_id/publication_number/section/offset/text_hash/excerpt`。
2. 写测试，要求 Context hash 覆盖消息、选中/排除 Chunk、预算、版本与限制，并且不包含 API Key。
3. 添加 `report_context_manifests`、`report_context_chunks`、`report_citations` 表及幂等迁移。
4. 实现 `PostgreSQLContextRepository.put_if_absent/get`，对同一 run/document 的 hash 冲突失败关闭。
5. 扩展 `ContextAssembler` 输出完整 citation binding，但让模型只看到短别名和原文。
6. 运行相关单测与真实 PostgreSQL 迁移测试，更新日志，提交并推送：`feat: persist deterministic report contexts`。

## Task 4：把 LEXICAL_RAG 接入首次文档分析

**文件：**

- 修改：`backend/idea/document_analysis.py`
- 修改：`backend/idea/execution.py`
- 修改：`backend/idea/runtime.py`
- 修改：`backend/idea/agent_schemas.py`
- 修改测试：`backend/tests/test_document_analysis.py`
- 修改测试：`backend/tests/test_execution.py`
- 修改测试：`backend/tests/test_runtime_corpus.py`

**步骤：**

1. 先写测试，要求 `initial_review_rag=true` 时禁止回退到瞬态全文证据包。
2. 写测试，要求模型引用只接受 Context 中的 `C1..Cn`，并解析为持久化 Chunk Citation；未知、错 Version、错 hash 均拒绝。
3. 给 `DocumentAnalysisService` 注入可选的 Initial Report RAG evidence provider；保留关闭特性时的旧实现。
4. 在 `WorkflowExecutor.ANALYZE_DOCUMENTS` 中先执行冻结检索和 Context 构造，再分析文档，并在全部成功后标记 deep reviewed。
5. 在 `build_runtime` 仅在两个依赖特性都开启且 PG 配置完整时组装 RAG 服务。
6. 运行相关单测，更新日志，提交并推送：`feat: analyze initial reports from lexical RAG`。

## Task 5：验证 Citation 并输出 JSON/Markdown

**文件：**

- 新建：`backend/idea/citations.py`
- 修改：`backend/idea/reporting.py`
- 修改：`backend/idea/run_store.py`
- 测试：`backend/tests/test_reporting.py`
- 新建测试：`backend/tests/test_citations.py`

**步骤：**

1. 先写 Citation verifier 测试，逐字段核验 run、document、Version、Chunk、公开号、section、offset、text hash 和 excerpt。
2. 写失败测试：模型伪造别名/公开号、Chunk 文本被改写、Citation 不属于源 Run 时均不得生成报告。
3. 实现不可变 `VerifiedCitation` 与 `CitationVerifier`；引用内容只从持久化 Chunk binding 构造。
4. 把 `schema_version` 升级为 `2.0`，在每个 feature mapping 和 deep-review document 中输出结构化 `citations`。
5. Markdown 为实质性 mapping 输出 `[C#] 公开号，章节` 与原文 excerpt；报告 composer 不接收创建 Citation 的权限。
6. 把 Citation/Context hash 写入报告 manifest 元数据。
7. 运行相关单测，更新日志，提交并推送：`feat: emit verified report citations`。

## Task 6：默认启用与一键 Docker 生命周期

**文件：**

- 修改：`config/ai4patent.json`
- 修改：`backend/tests/test_config.py`
- 修改：`start.sh`
- 修改：`stop.sh`
- 修改：`deploy/rag/compose.yml`
- 修改：`deploy/rag/rag.env.example`
- 修改：`tools/rag_infra.py`
- 新建/修改测试：`backend/tests/test_start_scripts.py`
- 修改测试：`backend/tests/test_rag_infra.py`

**步骤：**

1. 先写测试，要求仓库默认 `patent_corpus=true`、`initial_review_rag=true`、`followup_rag=false`。
2. 写脚本测试，要求 `./start.sh` 检查 Docker/Compose、至少 5G 可用空间、按需从 example 生成 mode 600 的 `rag.env`、启动完整 Compose、迁移、Bucket 和健康检查。
3. 要求 `./stop.sh` 只停止容器、不删除 volume；明确的 `--local` 兼容模式仍可用于纯 SQLite 开发。
4. Compose 不声明任何模型 API Key；前端现有 Base URL/Model/API Key 每 Run 输入保持不变。
5. 运行脚本和配置测试，并在测试栈上实际执行 start/status/stop，更新日志，提交并推送：`feat: make lexical RAG the runnable default`。

## Task 7：完整链路验收、文档和未完成项

**文件：**

- 新建：`tools/e2e_lexical_rag.py`
- 新建测试：`backend/tests/test_e2e_lexical_rag.py`
- 修改：`README.md`
- 修改：`deploy/rag/README.md`
- 修改：`development/followup-rag/README.md`
- 修改：`development/followup-rag/development-log.md`

**步骤：**

1. 写可重复 E2E 驱动：创建 Case、以内存 BYOK 创建 Run、等待完成、下载 JSON/Markdown、校验 Citation 与源 Chunk。
2. 运行全部离线测试：`cd backend && .venv/bin/python -m unittest discover -s tests -v`。
3. 启动 Compose 测试栈并运行 PostgreSQL/MinIO 集成测试与迁移检查。
4. 使用用户提供的临时模型凭证执行一次真实 DeepSeek 完整链路；不写文件、不打印、不记录 Key，结束后确认进程环境和仓库均无凭证。
5. 检查磁盘：`df -BG /home/ubuntu/patent`，不足 5G 立即停止并清理可安全重建的测试缓存。
6. README 写明新机器依赖、`git clone`、`./start.sh`、VS Code SSH 访问、BYOK 每次刷新消失、当前版本能力。
7. 开发文档明确未完成：追问聊天、Embedding/pgvector、RRF、reranker、引用前端精细展开、变体/法律状态增强。
8. 运行 secret scan、`git diff --check`、全量测试和远端 commit 对齐检查。
9. 更新日志，提交并推送：`docs: finalize lexical RAG report MVP`。

## 最终完成门

- `origin/develop` 与服务器 `develop` SHA 完全一致，工作树干净。
- 全量离线测试通过；PG/MinIO 集成测试通过；真实模型 E2E 生成可下载报告。
- JSON 与 Markdown 中每个 Citation 均可回查冻结 Version/Chunk，并通过 hash/offset/excerpt 校验。
- `./start.sh`/`./stop.sh` 可控制服务，不要求常驻，不删除数据。
- Git、文档、日志、报告、配置、进程输出均不包含模型 API Key。
- `/home/ubuntu/patent` 所在磁盘至少剩余 5G。

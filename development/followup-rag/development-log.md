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

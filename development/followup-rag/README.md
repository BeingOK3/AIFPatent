# Patent Corpus & RAG 开发域

本目录独立承载耐久专利全文语料、首次 IDEA 评审 RAG、评审后追问、Citation、Variant 和二次研究相关的设计与后续开发记录，不与项目既有 `docs/` 混合。

当前 `develop` 已完成首次报告 `LEXICAL_RAG` MVP：共享专利 Version/Chunk、PostgreSQL 词法检索、分析前 Context Manifest 和模型实际选择的可验证 Citation 已进入默认运行路径。`tools/e2e_lexical_rag.py` 会独立回查 PostgreSQL Context、allowed Version、Chunk 与模型选择，而不是让报告自行证明正确。Phase 4 已具备部署级 Embedding Provider、PostgreSQL 缓存、受冻结 Version 范围约束的 pgvector 精确召回、RRF/章节权重/多样性排序内核，以及报告内追问的 Thread/Turn、七节点 Workflow、跨 Turn Context、结构化回答、公开 API、SSE、取消、瞬时 BYOK 和 Citation 展开。追问 MVP 已默认启用并通过真实 DeepSeek 端到端运行态验收；未配置部署级 Embedding 时明确记录 `LEXICAL_ONLY`，不冒充混合召回。

仍未完成的后续范围：

- 首次报告默认 HybridRetriever 切换和真实跨语言 Embedding 评测；
- 可选语义 reranker 与首次报告/追问共用的离线检索、引用评测集；
- Citation 更精细的页码、段落定位与原网页跳转；
- 专利族 Variant、法律状态和二次研究增强；
- 多用户、组织、权限与租户隔离。

## 文件

- `architecture.md`：完整架构设计基线；
- `development-log.md`：本开发域只追加的工作记录。

## 约束

- 原 IDEA 固定 11 步 Workflow、历史报告和 Manifest 保持不可变；
- 新 Run 在深读全文入库后，逐步迁移到 `Feature × Patent` 的结构化 RAG 证据处理；
- 模型上下文由项目的版本化 `ContextAssembler` 装配并保存可审计 Manifest；LangChain 仅复用 Message、Prompt、Token 和 `ChatOpenAI` 等适配能力，不使用通用 Agent Memory 作为业务事实来源；
- SQLite 继续保存现有业务 Run/报告；PostgreSQL 已负责 Corpus、Chunk、检索审计和 Context/Citation 事实层，MinIO 保存耐久正文对象，Redis 基础设施已就绪；
- 只默认保存深度分析专利全文，普通候选仅保存检索元数据；
- 当前采用 `public_shared`：不建设用户、组织、分组和租户权限，所有访客都可查看全部业务内容；
- 本目录后续新增的 ADR、迁移说明、评测方案和开发日志均应留在本目录或其子目录；
- 通用项目文档仍保留在根目录 `docs/`；
- 运行时代码仍按现有职责放在 `backend/`、`frontend/` 和 `config/`，本目录只保存开发设计、计划、决策和验证记录。

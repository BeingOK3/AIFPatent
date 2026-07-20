# Patent Corpus & RAG 开发域

本目录独立承载耐久专利全文语料、首次 IDEA 评审 RAG、评审后追问、Citation、Variant 和二次研究相关的设计与后续开发记录，不与项目既有 `docs/` 混合。

当前方向不是只给报告页增加聊天框，而是先建立一套共享的专利 Version、Chunk、词法/向量检索和 Citation 基础设施，再同时供首次报告与追问使用。

## 文件

- `architecture.md`：完整架构设计基线；
- `development-log.md`：本开发域只追加的工作记录。

## 约束

- 原 IDEA 固定 11 步 Workflow、历史报告和 Manifest 保持不可变；
- 新 Run 在深读全文入库后，逐步迁移到 `Feature × Patent` 的结构化 RAG 证据处理；
- 目标生产数据底座为 PostgreSQL + pgvector、S3/MinIO 兼容对象存储和 Redis；SQLite 仅代表迁移前的当前实现；
- 只默认保存深度分析专利全文，普通候选仅保存检索元数据；
- 当前采用 `public_shared`：不建设用户、组织、分组和租户权限，所有访客都可查看全部业务内容；
- 本目录后续新增的 ADR、迁移说明、评测方案和开发日志均应留在本目录或其子目录；
- 通用项目文档仍保留在根目录 `docs/`；
- 运行时代码仍按现有职责放在 `backend/`、`frontend/` 和 `config/`，本目录只保存开发设计、计划、决策和验证记录。

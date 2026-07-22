# 专利态势分析开发域

本目录独立承载“具体技术方向或重点友商在近 X 个月新公开专利分析”的需求、架构、实施计划和追加式开发记录。

## 产品边界

- 新功能使用独立页面、API、业务包、SQLite 数据库、LangGraph Checkpoint 和报告目录。
- 现有 IDEA 固定 11 步 Workflow、追问 Workflow、Schema、Prompt、历史报告和前端交互保持冻结，不为本功能增加条件分支。
- 允许在共享 FastAPI 入口做最小路由装配；允许只读复用现有 Provider、Cache、结构化模型客户端和基础配置。
- 第一版不使用 Chunk 检索、Embedding、pgvector、RRF、Context Manifest 或 RAG。专利精读直接消费从单件公开文本构建的受控证据包。
- 所有分析结论只覆盖本次实际检索、严格过滤并成功抓取的公开文献，不宣称全球穷尽，也不构成法律意见。

## 文件

- `architecture.md`：MVP 需求分析、架构和数据/API 契约。
- `implementation-plan.md`：可提交、可验收的开发切片。
- `development-log.md`：只追加的开发和验证记录。
- `acceptance.md`：真实 Run、容器、凭证和已知限制验收结论。

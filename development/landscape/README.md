# 专利态势分析开发域

本目录独立承载“具体技术方向或重点友商在近 X 个月新公开专利分析”的需求、架构、实施计划和追加式开发记录。

## 当前实现快照

- 独立页面 `/landscape`、8 节点 LangGraph、Run/SSE/取消/报告下载和独立 SQLite 持久化已经落地。
- 支持技术方向、重点友商、技术方向与友商交集三种自动派生模式，并记录实际使用的友商别名。
- 默认由 SerpAPI Google Patents 引擎承担结构化检索和详情获取；Exa 与 Google Patents 直连可按本地配置启用为补充源。
- 公司柱状图统计严格过滤、跨查询去重后的唯一合格全集；精读先保证公司覆盖，再按各公司唯一专利数加权，同公司内优先可核验同族法域更多的专利，并支持同公司失败补位。
- 报告 Schema 当前为 `1.1.0`，技术聚类成员包含友商/权利人和申请日，逐件精读包含结构化全族状态。

## 产品边界

- 新功能使用独立页面、API、业务包、SQLite 数据库、LangGraph Checkpoint 和报告目录。
- 现有 IDEA 固定 11 步 Workflow、追问 Workflow、Schema、Prompt、历史报告和前端交互保持冻结，不为本功能增加条件分支。
- 允许在共享 FastAPI 入口做最小路由装配；允许只读复用现有 Provider、Cache、结构化模型客户端和基础配置。
- 专利精读不使用 Chunk 检索、Embedding、pgvector、Context Manifest 或 RAG，直接消费从单件公开文本构建的受控证据包；RRF 仅用于合并各 Query × Provider 的候选排名。
- 所有分析结论只覆盖本次实际检索、严格过滤并成功抓取的公开文献，不宣称全球穷尽，也不构成法律意见。

## 文件

- `architecture.md`：MVP 需求分析、架构和数据/API 契约。
- `implementation-plan.md`：可提交、可验收的开发切片。
- `development-log.md`：只追加的开发和验证记录。
- `acceptance.md`：真实 Run、容器、凭证和已知限制验收结论。
- `selection-report-v2-design.md`：已落地的唯一全集统计、候选/精读选样和报告 1.1 契约。
- `query-selection-v3-design.md`：中英文合并检索式、公司数量加权和同族覆盖优先的增量设计。
- `serpapi-provider-design.md`：已落地的 SerpAPI 检索、详情、凭证和配额边界。
- `network-provider-incident.md`：网络与 Provider 故障诊断及修复记录。

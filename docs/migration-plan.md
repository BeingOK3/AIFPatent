# AIFPatent LangChain / LangGraph 迁移计划

## 1. 项目边界

- 原项目 `/home/bok/code/patent_prototype/AI4Patent` 只读保留。
- 新项目 `/home/bok/code/patent_prototype/AIFPatent` 使用独立 Git 历史和远程仓库。
- 源码基线来自 AI4Patent 当前工作树，包含尚未提交但已通过测试的模型、Google Patents 和审计修复。
- 不复制 `.git`、OpenCode Skill 包、虚拟环境、运行数据库、Case/Run、缓存、日志和凭证。

## 2. 目标架构

- FastAPI、SSE 和前端 API 契约保持稳定。
- 固定 11 步 IDEA 流程迁移到 LangGraph `StateGraph`，模型不得自行决定控制流。
- LangChain 负责 OpenAI-compatible 模型适配和 Pydantic 结构化输出。
- Google Patents、EXA MCP、证据审计、报告和业务数据库继续由项目代码控制。
- LangGraph Checkpointer 只保存执行游标和轻量状态；业务 SQLite 继续保存权威专利数据。
- `run_id` 作为 LangGraph `thread_id`；API Key 只进入运行时 Context，不进入 State、Checkpoint、数据库或日志。

## 3. 实施顺序

1. [x] 建立独立源码基线和提交规范。
2. [x] 移除 OpenCode Runtime、旧 API、健康检查、安装逻辑和凭证回退。
3. [x] 分离领域、基础设施和编排边界。
4. [x] 建立 Typed Graph State、固定拓扑、重试、超时和事件桥接。
5. [x] 迁移输入、规划、检索、文献分析、结论、价值、审计和报告节点。
6. [x] 接入 LangChain BYOK 模型工厂并保留厂商兼容策略。
7. [x] 保持 Case/Run API、SSE、调试日志和前端兼容。
8. 完成离线、并发、恢复、降级、真实模型和 Manifest 回归。
9. 推送独立 GitHub 仓库并复验原项目零变化。

## 4. 完成门槛

- 新项目不包含 OpenCode 可执行依赖或配置目录。
- Graph 固定为 11 个权威业务节点，支持取消、重试和进程恢复。
- 所有业务写入幂等；节点重放不会重复插入 Evidence、审计或报告。
- API Key 不出现在源码、Git、SQLite、Checkpoint、JSONL 或浏览器存储。
- 现有单元/API/前端测试迁移后通过，并新增 LangGraph 拓扑与恢复测试。
- 至少完成 quick、standard、并发、Provider 降级和服务恢复真实回归。
- 报告继续满足中文判断、1–5 分评分、可点击专利链接和 Manifest 哈希校验。

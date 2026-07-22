# 专利态势分析 MVP 实施计划

日期：2026-07-22

## 提交 1：技术基线

- 新增开发域 README、架构和实施计划；
- 建立只追加开发日志；
- 明确 IDEA 冻结边界、非 RAG 边界和完成门。

验收：文档路径、术语、模式、窗口、数据和 API 契约一致；`git diff --check` 通过。

## 提交 2：领域与持久化

- 新增 `backend/landscape/schemas.py`；
- 新增独立 SQLite Repository；
- 新增不可变报告 Store；
- 覆盖输入、状态转换、幂等写入、Manifest 和凭证不落库测试。

验收：目标测试通过；与 IDEA SQLite 文件无交叉写入。

## 提交 3：检索与严格范围

- 查询规划 Schema/Service；
- Provider 调用、日期窗口、友商别名、去重、预算和覆盖统计；
- 使用 Fixture 测试边界日期、未知日期、越界、友商误匹配和重复项。

验收：正式候选全部满足公开日和模式范围；排除计数可回读。

## 提交 4：全文精读与聚类

- 全文 fallback 抓取；
- 非 RAG 直接证据包；
- 逐件结构化分析和证据绑定；
- 无向量聚类及成员完备性校验。

验收：未知证据、公开号越界、聚类遗漏/重复 fail closed；部分文献失败可限制完成。

## 提交 5：Workflow、API 和运行时

- 固定 Landscape LangGraph；
- TaskManager、SSE、取消、重启失败语义和 BYOK；
- 在共享 `main.py` 做最小 Router/页面装配；
- 报告 JSON/Markdown/CSV API。

验收：API 生命周期、凭证清理、取消隔离和恢复语义测试通过。

## 提交 6：独立页面

- 新增 `landscape.html/js/css`；
- 历史、输入、步骤、图表、聚类、逐件精读和限制；
- 不修改 `frontend/app.js` 的 IDEA 状态机。

验收：Node 语法、DOM/API 契约、XSS 安全和响应式布局测试通过。

## 提交 7：运行验收与修正

- 全量离线测试；
- Docker 镜像重建和四服务健康；
- 使用临时 DeepSeek BYOK 完成一条小预算真实 Run；
- 回查 SQLite、报告 Manifest、CSV、模型凭证和 IDEA 回归；
- 追加开发日志。

验收：至少一件真实公开专利成功精读并生成聚类/统计；失败和限制如实显示；两提交一次推送远程。


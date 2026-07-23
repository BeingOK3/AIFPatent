# 专利态势分析 MVP 验收记录

状态：`ACCEPTED_HISTORICAL_MVP_WITH_POST_MVP_ADDENDUM`

初次验收：2026-07-22

最近补充：2026-07-23

> 本文前半部分保留最初 MVP Run 的验收事实。此后检索 Provider、查询覆盖、选样和报告契约已有增强；当前能力见文末补充及 `architecture.md`。

## 可用入口

- 页面：`http://localhost:8001/landscape`
- 首页入口：打开 `http://localhost:8001`，点击右上方“专利态势分析”卡片。
- API：`/api/landscape/*`
- 启动：项目根目录执行 `./start.sh`
- 停止并保留数据：项目根目录执行 `./stop.sh`

页面中的 Base URL、模型和 API Key 按 Run 临时使用。API Key 不写入浏览器存储、SQLite、报告或 Git；服务重启后，未完成 Run 会要求使用新凭证创建不可变重跑任务。

## 实际验收结果

真实 Run `964ce4f5-1c66-418b-a92e-8091dfffb088` 使用：

- 模式：`TECHNOLOGY`；
- 方向：`liquid cooling system`；
- 公开日：2025-07-22 至 2026-07-22，包含两端；
- 候选上限：10；
- 精读上限：1；
- 模型：`deepseek-v4-flash`。

结果：

- 8 个固定 LangGraph 节点全部成功；
- 5 件严格窗口内唯一候选；
- 1 件成功精读：`US20260082513A1`；
- 1 个确定性单文献技术簇；
- JSON、Markdown、CSV 和 Manifest 均生成并通过完整性读取；
- Run 状态为 `COMPLETED_WITH_LIMITATIONS`，没有隐藏 Provider 失败或越界排除。

## 验证清单

- Landscape 单元测试：29 个通过；
- 既有前端、容器、启动脚本针对性回归：12 个通过；
- `node --check frontend/landscape.js`：通过；
- Docker：App、PostgreSQL、Redis、MinIO 均为 healthy；
- `/landscape`：HTTP 200；
- Markdown/CSV 下载：HTTP 200；
- Landscape 配置快照字段仅为 `base_url`、`credential_source`、`model`；
- 仓库敏感 Key 模式扫描：无命中；
- IDEA 业务包和 `frontend/app.js`：本开发域未修改。

## 已知限制

- Google Patents 当前可能触发限流或自动化保护；系统会在报告中标记 `PROVIDER_FAILURE`，不会把单 Provider 结果描述为完整全球检索。
- Exa 搜索结果有时缺少公开日；系统会以受控详情抓取补全元数据，仍无法确认日期时才排除。
- 当前权利人和同族成员受 Provider 数据能力限制；无法证明时输出 `PARTIAL` 或 `UNAVAILABLE`。
- MVP 聚类不使用向量；大样本聚类质量和吞吐量需要后续用标注集评估。
- 本报告用于技术研究，不构成法律意见或全球穷尽检索结论。

## MVP 验收时的下一批建议（历史）

1. 为 Google Patents 增加服务级退避可视化和备用专利数据 Provider。
2. 建立 20～50 个技术方向/友商的人工标注验收集，评估召回、日期准确率、精读字段和聚类一致性。
3. 补充确认同族成员与当前权利人专用数据源，再扩展同族法域统计。
4. 在真实使用量稳定后再评估队列化 Worker、多租户权限和向量聚类，不在 MVP 提前引入 RAG。

## 2026-07-23 后 MVP 补充验收

- SerpAPI Google Patents 已成为默认启用的结构化检索和详情 Provider；Exa 与 Google Patents 直连默认关闭，可由本地配置显式启用。
- 技术方向规划同时保留原始语言和英文词；友商与联合模式逐一覆盖所有友商，不再因固定查询总上限跳过靠后的公司。
- 真实四友商场景生成 8 条检索式，每家公司均有独立原始语言/英文查询，规划覆盖校验通过。
- 公司柱状图改用严格过滤、跨查询去重后的唯一合格全集，不受 `candidate_limit` 和 `analysis_limit` 截断。
- 候选选择已加入 Query × Provider 排名 RRF、查询/Provider 覆盖和技术文本匹配；友商模式执行公司覆盖配额，精读执行同族去重、公司覆盖和抓取失败补位。
- 报告 Schema 升级为 `1.1.0`；聚类成员展示友商/当前权利人和申请日，逐件报告展示结构化全族状态。
- SerpAPI 搜索与详情已经使用本地私密配置完成真实响应验证；真实 Key 仍只保存在被忽略的本地 JSON 中。
- 详细实现与验证证据以 `development-log.md` 中日期更晚的追加记录为准。

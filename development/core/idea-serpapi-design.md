# IDEA SerpAPI Provider 增量设计

状态：`APPROVED_FOR_IMPLEMENTATION`

日期：2026-07-23

## 问题

SerpAPI Provider 已实现并接入“专利态势分析”，但 IDEA 的 `build_runtime()` 仍只装配 `google_patents_local` 和 `exa_mcp`。当前部署为避免 Google 直连超时和匿名 Exa 429，默认关闭了这两个 Provider，因此 IDEA 的检索服务得到空 Provider 列表，首轮必然以 `PROVIDERS_UNAVAILABLE` 停止。

## 目标与边界

- 在不修改 IDEA 固定 11 步 LangGraph、Case/Run 不可变语义、初次报告 RAG 和追问 RAG 的前提下，把 `SerpApiPatentProvider` 加入 IDEA 候选检索和全文获取。
- 复用现有本地 `config/provider-credentials.local.json`、Provider Cache、`ProviderRunner`、跨源合并、筛选、Tool Call 审计和 Debug；不增加浏览器 Key 字段。
- IDEA 的评估日门禁和本地候选筛选继续作为权威规则；Provider 返回内容不能直接改变新颖性/创造性结论。

## 运行时装配

默认 Provider 顺序：

1. `serpapi_google_patents`：当前环境主检索与首选详情源；
2. `google_patents_local`：仅在配置启用且网络可达时作为补充；
3. `exa_mcp`：仅在配置启用且具备额度时作为语义补召回。

每个 Provider 的超时从统一配置读取。运行时测试必须证明默认配置至少装配一个 Provider，且当前默认值只装配 SerpAPI。

## 检索调度

- 保留“每轮查询 × 已启用 Provider”的完整可审计调用语义。
- 同一 Run 内每个 Provider 串行，避免多条 IDEA 查询同时消耗 SerpAPI 额度或触发突发限流；不同 Provider 之间仍可并行。
- SerpAPI 的 Key 缺失、鉴权失败或额度/速率限制首次出现后，对当前 Run 打开熔断；剩余查询记录为 `DISABLED`，不得继续扣费。
- Exa 重新启用后首次 HTTP 429 同样对当前 Run 熔断；Google 自有持久风控门保持不变。
- SerpAPI 的“无结果”继续作为 `EMPTY` 成功状态参与饱和判断，不得误报 Provider 降级。

## 全文获取

- 健康 Provider 优先于本轮全失败 Provider。
- 同等健康状态下按 SerpAPI、Google 直连、Exa 排序。
- SerpAPI 通过 `google_patents_details` 返回元数据、摘要、权利要求、同族与说明书链接；任一详情失败继续尝试后续 Provider。
- 全文、Evidence、Corpus Version 和 Citation 的现有验证规则不变。

## 安全与可观测性

- IDEA 页面、Run 请求、SQLite、Debug、报告和 Manifest 不保存 SerpAPI Key。
- Tool Call 继续只记录 Provider、操作、状态、耗时、结果数和稳定错误码。
- `httpx` 完整 URL 日志保持关闭；缓存键和内容不包含 Key。

## 验收

1. 默认 IDEA Runtime Provider 列表为 `serpapi_google_patents`，不再为空。
2. 两条并发规划查询对同一 Provider 实际串行执行。
3. SerpAPI 鉴权/额度错误后只发生一次真实调用，后续为熔断状态。
4. 全文回退在同等健康状态下优先 SerpAPI，并能回退到其他 Provider。
5. 既有 Retrieval、Runtime、Config、SerpAPI、Debug 与安全测试通过。
6. 容器内 IDEA Runtime 可读取本地 Secret，并完成一次受控搜索和详情获取；日志无 Key。

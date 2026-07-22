# SerpAPI Google Patents Provider 设计

状态：`IMPLEMENTED_WITH_LIVE_PROBE_PENDING`

日期：2026-07-22

## 目标

在不修改 IDEA 业务状态机的前提下，为专利态势分析增加第三个可审计检索与详情 Provider，解决当前部署网络无法直连 Google Patents、匿名 Exa MCP 又可能耗尽额度时的可用性问题。

## 接口映射

检索请求调用 `GET https://serpapi.com/search.json`：

```text
engine=google_patents
q=<程序生成的中英文/友商查询>
after=publication:YYYYMMDD
before=publication:YYYYMMDD
num=10..100
patents=true
scholar=false
dups=language
```

`organic_results[]` 映射为 `SearchHit`：`patent_link`、`publication_number`、`title`、`snippet`、`priority_date`、`filing_date`、`publication_date`、`assignee` 和 `country_status`。后端仍执行严格日期、申请人和公开号过滤，不能信任 Provider 已经完整过滤。

详情请求使用 `engine=google_patents_details` 和 `patent_id=patent/<PUBLICATION>/<LANGUAGE>`。摘要、权利要求、申请号、申请/公开日期、当前申请人、Family ID 和全球申请信息映射为 `FetchedDocument`。如果详情结果没有说明书正文，必须如实保留空值，分析证据包不得把摘要或权利要求伪装成说明书。

## 凭证生命周期

- Provider 从 `config/provider-credentials.local.json` 的 `serpapi.api_key` 读取凭证。
- 真实本地文件加入 `.gitignore` 和 `.dockerignore`，仓库只提交 `config/provider-credentials.example.json`。
- Docker Compose 通过只读 Secret 挂载本地文件，镜像层不包含真实凭证；本地模式直接读取同一文件。
- 普通 Search API 按官方协议使用 `api_key` query 参数；应用不记录最终 URL，也不把 HTTP 异常原文透传到 Debug，以避免 URL 中的 Key 泄露。若未来切换 SerpAPI MCP，可使用其 Bearer Header 方式。
- Runtime、日志、SQLite、报告和 Debug 只能记录来源 `local_json`，不能记录值。
- 401/403/429 和响应中的 `error` 转换为稳定错误码；错误正文必须脱敏。

## 配额控制

- 同一 Provider 串行执行，避免突发并发。
- 使用本地 Provider Cache；SerpAPI 官方精确缓存也保持开启，不发送 `no_cache=true`。
- 429 后对当前 Run 打开 Provider 熔断，不再继续消耗调用。
- 搜索每个确定性查询只调用一次；详情仅用于日期补全和已选精读文献。
- `num` 不低于 SerpAPI 要求的 10，返回后按本地 `per_query_limit` 截断。

## 三路调度建议

| 策略 | SerpAPI | Exa MCP | Google 直连 | 适用场景 |
|---|---|---|---|---|
| COMPLETE | 全部查询 | 全部查询 | 健康时全部查询 | 验收、最高覆盖、成本最高 |
| BALANCED | 全部查询 | 技术语义查询 | 健康时补充/抽查 | 推荐生产默认 |
| ECONOMY | 全部查询 | 仅覆盖不足时 | 关闭或仅故障回退 | SerpAPI 额度紧张 |

首个实现采用 `COMPLETE` 的可观测 fan-out 语义，但无凭证 Provider 必须明确报错/跳过；不在本次直接加入自动策略选择，以免未经真实命中对比就固化错误分配。完成至少三组中英文、友商和组合模式对比后再落地 `BALANCED`。

## 验收

1. Provider 单测覆盖查询参数、解析、详情映射、API 错误和密钥脱敏。
2. Landscape 前端和 Run 请求不包含 SerpAPI Key；本地私密 JSON 和仓库模板字段一致。
3. Run 配置快照、Debug、数据库和报告均不包含密钥。
4. 三个 Provider 调用状态在 Debug 中独立显示并参与统一去重。
5. 使用用户测试 Key 完成一次受控检索，确认至少返回结构化字段；不得在命令、日志或提交中暴露 Key。当前沙箱外部请求授权受限，本项保留为部署环境联调，不影响本地契约、容器 Secret 与三 Provider 调度验收。

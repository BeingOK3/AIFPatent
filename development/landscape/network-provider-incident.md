# Landscape Provider 网络与检索故障复盘

状态：`IMPLEMENTED_AND_VERIFIED`

日期：2026-07-22

## 现象与证据

最新 Run `8383baa5-4f17-4b60-b2b7-2a6573226763` 完成但没有候选，18 次 Provider 调用均显示失败或禁用。逐项检查后的事实如下：

- 容器能解析 `patents.google.com`、`serpapi.com`、`mcp.exa.ai` 和模型服务域名，Docker DNS 正常。
- 容器不带凭证的 HTTPS 探测中，SerpAPI、Exa 和模型服务可以建立连接；只有 `patents.google.com` 在 12 秒内连接超时，属于目标路由不可达，不是整个容器断网。
- SerpAPI 实际返回 HTTP 200；名称组查询返回“没有结果”，代码误记为 `SERPAPI_ERROR`；申请人字段组把多个 `assignee:` 放入 `q`，被 Google Patents 判定为过多嵌套操作符。
- Exa MCP 初始化可以连接，但工具调用持续返回 HTTP 429，属于匿名服务额度/限流，不是网络不通。
- `httpx` INFO 日志打印了带 `api_key` 查询参数的完整 SerpAPI URL，造成凭证进入容器标准输出日志。
- 宿主机代理变量指向 `127.0.0.1:7897`，当前端口无服务；这会影响宿主机直接启动的进程，但 Compose 运行时没有继承这些代理变量，因此不是本次容器 Run 的主因。

## 修复决策

1. SerpAPI 作为当前环境唯一默认启用的可靠主 Provider。
2. 将纯 `assignee:` OR 查询解析为一个宽松 `q` 和官方逗号分隔 `assignee` 参数；含逗号的公司名使用括号包裹。
3. 将 SerpAPI 的“没有结果”响应映射为成功空集 `EMPTY`，与真正的鉴权、额度和语法错误区分。
4. Exa 首次出现 429 后立即对当前 Run 熔断；默认关闭匿名 Exa，配置有效额度后再启用。
5. 默认关闭当前网络无法访问的 Google Patents 直连；代码与配置项保留，部署到可访问 Google 或配置有效代理的环境后可重新启用。
6. 将 `httpx`/`httpcore` 日志级别提升为 WARNING，Provider Debug 继续记录脱敏状态、耗时和稳定错误码，不记录最终请求 URL。
7. 重建应用容器以删除旧容器实例及其标准输出日志；由于凭证曾进入本机容器日志，建议随后在 SerpAPI 控制台轮换 Key。

## 验收标准

- 单测证明申请人查询使用独立 `assignee` 参数，且 `q` 不含 `assignee:`。
- 单测证明无结果响应产生 `EMPTY`，而不是 `ERROR`。
- Exa 429 后剩余查询均为快速熔断，不再继续请求。
- 默认运行时只装配 SerpAPI，Google 直连和匿名 Exa 不制造全局 Provider Failure。
- 新容器日志不再出现 `api_key=`、SerpAPI Key 或完整外部请求 URL。
- 容器内受控 SerpAPI 查询至少返回一条结构化专利结果。

## 验收结果

- 新 Provider 使用结构化申请人参数真实检索返回 10 条结果。
- 服务健康状态为 `ok`，SerpAPI 为 `configured`，Exa 和 Google 直连为 `disabled`。
- 旧持久化日志中的 6 个 Key 值已原位替换为 `[REDACTED]`，新容器 stdout 和应用日志真实 Key 命中均为 0。
- `QUARTER` 标签与一个月日期不一致的问题同时修复；后端现将季度范围规范为结束日前 3 个自然月。
- 49 项 Landscape 测试和 74 项 Provider、健康、配置、容器相关测试通过，四个 Compose 服务均为 healthy。

# 友商多别名导致 SerpAPI 空结果修复

日期：2026-07-27

状态：`APPROVED_FOR_IMPLEMENTATION`

## 1. 故障

最新专利态势 Run `71b23172-67b7-4905-b9e8-cd0d7a6bd41d` 输入友商“英伟达”，约 15 秒后以 `COMPLETED_WITH_LIMITATIONS` 结束，但没有候选、精读或聚类输出。

Debug 事实：

- 别名 Agent 正常得到 `NVIDIA`、`NVIDIA Corporation`、`英伟达公司`、`NVDA` 等名称；
- 查询计划只生成一条友商查询；
- SerpAPI 状态为 `EMPTY`，原始命中数为 0；
- 后续过滤、详情、精读和聚类均消费空集合，因此快速完成；
- 报告只记录 `CLUSTER_FAILURE`，没有直接说明检索本身返回空集。

## 2. 根因

友商模式把每个名称写成：

```text
assignee:"英伟达" OR assignee:"NVIDIA" OR
assignee:"NVIDIA Corporation" OR assignee:"NVDA"
```

`SerpApiPatentProvider` 会提取全部 `assignee:` 值并传入 SerpAPI `assignee` 参数。虽然官方参数允许逗号分隔多个申请人，但真实对比证明，这不等同于“同一主体多个别名任选其一”，而会把别名组合成过度收窄的参与人过滤条件。

同一时间窗真实探针：

- 当前多别名申请人参数：0 条；
- 单一 `assignee:"NVIDIA"`：50 条；
- 普通 `NVIDIA` 查询：50 条。

因此问题不是时间窗内没有 NVIDIA 专利，而是把主体别名误当成多个独立申请人过滤条件。

## 3. 修复

仅友商模式改为普通名称 `OR` 组：

```text
"英伟达" OR "NVIDIA" OR "NVIDIA Corporation" OR "NVDA"
```

边界：

- 仍然是一家公司一条检索式，不恢复中英文拆分查询；
- 返回后继续用 `assignee` 字段和已确认别名做严格过滤，因此普通文本召回不会直接进入正式结果；
- `assignee:` 语法和 Provider 参数能力继续保留，供明确的单申请人查询使用；
- 联合模式原本已使用普通友商名称组，无须改变。

## 4. 空结果语义

当所有已执行 Provider 均为 `EMPTY` 且 `raw_hit_count = 0` 时，报告增加：

```text
SEARCH_EMPTY：已启用的检索 Provider 未返回任何原始专利命中。
```

当 Provider 有原始命中但严格过滤后唯一合格数量为 0 时，报告增加：

```text
NO_ELIGIBLE_PATENTS：检索有返回，但没有专利同时满足公开日和友商范围。
```

两者不能替代 Provider 错误；鉴权、额度、网络和超时仍使用 `PROVIDER_FAILURE`。

## 5. 验收

1. 仅友商计划包含所有确认别名，但不生成多个 `assignee:` 过滤值；
2. 联合模式和技术方向模式保持 V3 查询数量；
3. `SEARCH_EMPTY` 与 `NO_ELIGIBLE_PATENTS` 分别有回归测试；
4. 使用本地私密 SerpAPI 配置验证英伟达查询返回真实命中；
5. 新建 Run 能进入严格过滤、详情和精读，而不是 0 候选快速空跑；
6. 凭证不进入日志、Debug、文档或 Git。

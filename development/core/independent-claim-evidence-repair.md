# IDEA 独立权利要求证据修复设计

## 1. 现象

另一台机器上的 IDEA Run 连续失败于：

```text
ReportRetrievalError: mandatory independent-claim evidence is missing for cv-...
```

相同 `corpus_version` 重复出现，说明失败来自确定性的全文/Chunk 数据，而不是模型或临时网络波动。

## 2. 根因

当前链路存在三个断层：

1. `FetchedDocument` 只要摘要、权利要求或说明书任一非空就可被 Provider 判为成功；IDEA 全文抓取没有提前验证独立权利要求。
2. `PatentChunker` 只识别 claims 正文行首的 `1.`、`2.`，没有使用 Provider 已保存的 `section_spans.claims`。结构化 claim 数组缺少正文编号时会被合并成一个 `claim_number=NULL` Chunk。
3. 报告 RAG 最后才执行强制独立权利要求门禁。此时 Version 已冻结、抓取步骤已 checkpoint，重试会继续命中同一版本并得到相同错误。

SerpAPI 官方合同把 `claims` 定义为权利要求字符串数组；但单件详情仍可能缺失该字段或返回无法识别的内容。因此不能把“详情 HTTP 成功”等同于“满足 IDEA 深读证据要求”。

## 3. 修复决策

### 3.1 不放宽证据门禁

首次报告仍要求每篇进入深读范围的专利具有摘要和至少一项可识别独立权利要求。不把摘要或说明书伪装成权利要求，也不让模型猜测缺失内容。

### 3.2 提前执行全文质量检查

IDEA 的 `_fetch_with_fallback` 在接受 Provider 结果前检查独立权利要求：

- 合格：继续持久化并进入 Corpus；
- 不合格：记录稳定错误码 `INDEPENDENT_CLAIM_MISSING`，尝试下一个 Provider；
- 所有 Provider 均不合格：该专利不进入深读范围，并保留脱敏 limitation/debug 记录。

若首选深读专利不合格，则从本轮已筛选且满足日期、相关性和公开号要求的候补专利中补位，直到恢复原目标数或候补耗尽。候补不重新搜索，不降低相关性阈值。

### 3.3 扩展结构化 claims 解析

Chunker 解析顺序：

1. 优先使用正文中的明确 claim 编号；
2. 正文无法分项时，验证并使用 `section_spans.claims` 的边界和标签；
3. 两者均不可用时保留 unknown claims Chunk，但不把它视为独立权利要求。

新逻辑使用 `claims-paragraphs-v2`。同一 Corpus Version 可保留旧 Chunk 以满足不可变审计要求，但检索只选择每个 Version 最近成功写入的一组 Chunker 结果，避免 v1/v2 重复进入上下文。

### 3.4 可观测性

兜底报告错误同时输出公开号、claims Chunk 数量和 Chunker 版本；日志不记录权利要求全文或 API Key。这样跨机器问题可以直接定位到具体专利和解析批次。

## 4. 验收

- 无正文编号但具有合法 claim spans 的文档可生成独立/从属 Chunk；
- claims 缺失的 Provider 成功响应会触发同专利 Provider fallback；
- 首选专利均失败时使用相关候补补足，不抓取阈值以下结果；
- PostgreSQL Chunk、词法和向量读取仅使用每个 Version 最新 Chunker 批次；
- 报告最终门禁继续拒绝真实缺失的独立权利要求；
- 现有日志、checkpoint、API Key 不泄露正文或凭据。

## 5. 已失败 Run 的处理

既有失败 Run 已冻结旧 Corpus Version 和 checkpoint，不在重试时原地改写不可变证据。部署修复后应新建 Run；新 Run 会重新执行全文质量检查，并在遇到相同专利时回退或补位。

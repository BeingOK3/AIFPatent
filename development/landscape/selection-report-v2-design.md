# 专利态势分析选样与报告 V2 设计

日期：2026-07-23

状态：`IMPLEMENTED_AND_VERIFIED`

实现：报告 Schema `1.1.0`，对应选样、统计、聚类成员和全族状态契约均已进入代码与回归测试。

## 1. 目标

修正大结果集下“搜索引擎前 100、再直接取前 20”的偏差，并更新报告：

- 用严格过滤、跨查询去重后的全部唯一专利统计公司专利数量；
- 取消申请日趋势图，改为公司专利数量柱状图；
- 候选和精读选择可解释，并避免单一友商无意占满预算；
- 技术聚类成员展示公开号、友商/当前权利人和申请日；
- 逐件精读展示可核验的全族状态。

## 2. 三个集合

### 2.1 唯一合格全集

`eligible_unique_universe` 是所有 Provider 命中经过以下处理后的集合：

1. 公开号有效；
2. 公开日在用户窗口内；
3. 友商/联合模式下当前申请人与任一确认友商名称或别名匹配；
4. 按公开号、申请号和已确认同族身份去重。

公司柱状图必须使用该全集。不能使用原始命中，因为同一专利跨检索式/Provider 会重复；不能使用候选集，因为 `candidate_limit` 已截断第 101 件之后的专利。

### 2.2 候选集

候选集受 `candidate_limit` 限制。专利先按可审计检索分排序：

- RRF：综合每个 Query × Provider 的原始排名；
- Query 覆盖：被多少不同中英文检索式命中；
- Provider 覆盖：被多少独立 Provider 命中；
- 技术文本匹配：标题和摘要片段对计划方向词的确定性匹配。

公司覆盖不混入相关性分，而在排序后作为选样约束：

- 友商/联合模式采用 `40% 均衡基线 + 60% 按唯一合格数量比例`；
- 某友商不足其配额时，剩余名额按各公司下一件最高分专利重新分配；
- 技术方向模式不猜测企业集团关系，按 Provider 的当前申请人规范化文本统计，并以轮转上限避免一个完全相同申请人占满候选集；
- 每件入选专利保存分数和入选原因，Debug 不保存正文。

### 2.3 精读集

精读集受 `analysis_limit` 限制：

1. 同一确认同族优先只精读一个代表；
2. 友商/联合模式先保证每个有合格候选的友商至少一件；
3. 其余名额按公司轮转和候选分补齐；
4. 全文抓取失败时，从同一公司下一件候选补位，再使用全局下一件补位；
5. 技术聚类仍只对成功精读集合生成，报告必须明确聚类范围，不能称为全部唯一专利的完整聚类。

## 3. 公司统计口径

报告字段：

```json
{
  "company_patent_counts": [
    {
      "company": "Huawei",
      "patent_count": 42,
      "share": 0.35,
      "source": "CONFIRMED_COMPETITOR"
    }
  ]
}
```

- 友商/联合模式把所有已确认名称和实际使用别名归并到用户输入的主名称；
- 技术方向模式只做 NFKC、大小写、标点和空白规范化，相同规范键归并，展示最完整的原始申请人名称；
- 缺少申请人的唯一专利归入“未知权利人”；
- `patent_count` 统计唯一专利，不统计原始命中次数；
- `share = patent_count / unique_candidate_count`。

## 4. 聚类成员

Clusterer 的成员合同继续只保存公开号，防止模型改写 Provider 元数据。报告层按公开号确定性连接成功精读文档，新增：

```json
{
  "members": [
    {
      "publication_number": "CN...",
      "competitor": "Huawei",
      "current_assignee": "Huawei Technologies Co., Ltd.",
      "filing_date": "2026-01-01"
    }
  ]
}
```

`competitor` 只有在当前权利人能匹配本次确认友商时才输出；否则为 `null`。申请日和当前权利人只能来自 Provider。

## 5. 全族状态

精读报告新增 `family_status`：

```json
{
  "data_status": "PARTIAL",
  "family_id": "...",
  "overall_legal_status": "MIXED",
  "jurisdictions": ["CN", "EP", "US"],
  "members": [
    {
      "jurisdiction": "US",
      "application_number": "US...",
      "filing_date": "2025-01-01",
      "legal_status_category": "ACTIVE",
      "legal_status": "...",
      "is_current_application": false
    }
  ]
}
```

- 数据来自 SerpAPI `worldwide_applications` 或其他 Provider 的等价已确认结构；
- 有部分结构化成员时为 `PARTIAL`，没有可确认成员时为 `UNAVAILABLE`；
- 当前 Provider 不能证明全球完整性，因此本版本不输出 `COMPLETE`；
- `overall_legal_status` 只从成员的结构化状态归纳为 `ACTIVE | INACTIVE | MIXED | UNKNOWN`；
- 不从标题、国家代码或模型文本猜测法律状态。

## 6. 页面和下载

- 删除“申请日趋势”柱状图；
- 增加“唯一合格专利的公司分布”单一柱状图；
- 保留公开法域布局作为文本/统计数据，但不与公司柱状图争用主图；
- 聚类卡片逐行展示成员公开号、友商/权利人、申请日；
- 精读卡片展开全族状态和成员；
- Markdown 与 JSON 使用同一口径；CSV 增加全族状态摘要字段。

## 7. 兼容和不可变性

- 不修改 IDEA、首次报告 RAG 或追问模块；
- 历史报告保持原 Schema；新 Run 使用 `landscape-report/1.1.0`；
- 旧 Run 不回填或改写，新展示代码对缺少 V2 字段的历史报告显示“暂无数据”；
- 不新增 Docker 依赖，不保存 API Key、Provider 原始全文或模型隐藏输出。

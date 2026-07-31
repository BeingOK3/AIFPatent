# 可扩展专利态势分析 Agent 独立规格

状态：`draft-for-review`

版本：`v1`

建立日期：`2026-07-31`

适用分支：`feature/agent-upgrade`

目标版本：Workflow `3.x`，Report Schema `landscape-report/3.0.0`

事实源：PostgreSQL

## 0. 文档定位

本文件独立定义专利态势分析的业务需求、领域模型、上下文边界、Graph、
持久化、评测和实施顺序。实施本规格不要求先理解其他态势分析规格，也不得
从历史实现反推产品需求。

规范用词：

- “必须”：实现和测试不得省略；
- “应当”：默认实施，偏离时必须记录理由；
- “可以”：不影响核心契约的可选能力。

核心原则：

> 模型负责有界的技术语义抽取、命名和解释；程序负责身份、成员关系、
> 时间、统计、预算、证据回填、覆盖审计和状态迁移。

## 1. 产品目标

系统面向给定公司、技术方向和公开时间窗，对检索范围内全部去重且合格的
专利分析单元完成：

1. 建立完整、可审计的专利技术方向账本；
2. 构建跨公司可比较的统一技术分类；
3. 生成各公司的技术组合、重点方向和差异化布局；
4. 在时间证据充分时识别技术活动的变化方向；
5. 生成跨公司的共同方向、差异方向和趋势解释；
6. 选择少量代表专利进入可选精读；
7. 保证所有高阶结论可回溯到专利、文本证据和程序统计；
8. 在 20、100、200 乃至更大分析集合下保持固定的单次模型上下文上限。

本系统输出的是“检索范围内的专利态势”，不是全球专利完整性声明，也不是
法律意见。

## 2. 分析意图

### 2.1 三种业务模式

| 模式 | 输入 | 业务问题 | 允许的核心输出 |
|---|---|---|---|
| `COMPANY_PORTFOLIO` | 公司 + 时间 | 公司在本时间窗公开了哪些技术组合 | 技术版图、重点领域、公司间共同与差异方向 |
| `TECHNOLOGY_LANDSCAPE` | 技术方向 + 时间 | 该技术领域由哪些技术分支和申请人构成 | 统一技术分类、主要申请人、领域时间变化 |
| `COMPETITIVE_TECHNOLOGY` | 公司 + 技术方向 + 时间 | 指定技术中各公司的路线和变化有何差异 | 公司路线、份额结构、共同与差异方向、时间趋势 |

系统必须根据输入推导模式。显式模式与输入不一致时必须拒绝，而不是静默
改写。

### 2.2 只有公司输入时的边界

只有公司、没有技术方向时，检索可能覆盖通信、芯片、终端、软件、车辆等
彼此无关的业务线。此时：

- 报告必须称为“公司专利技术组合”或“公司技术布局”；
- 可以比较公司是否在相同技术大类中存在布局；
- 不得把弱相关专利包装成同一技术路线；
- 不得仅为满足“跨公司趋势”数量而制造共同方向；
- 时间证据不足时不得使用增长、下降、加速或转移等表述。

### 2.3 时间结论等级

程序根据有效时间桶数量和样本量决定可用结论等级：

| 有效时间桶 | 允许结论 |
|---:|---|
| 1 | 当前布局、技术组合、代表方向 |
| 2 | 前后期差异观察，不输出强趋势 |
| ≥ 3 | 在样本门槛通过后允许 `GROWING`、`DECLINING`、`SHIFTING` 等趋势 |

模型不能自行提高结论等级。

## 3. 非目标

- 不对全部合格专利执行全文精读；
- 不让模型直接生成或修改公司 ID、专利身份、Family 身份和 Evidence ID；
- 不让模型直接计算专利数量、占比、增长率、斜率或时间桶；
- 不把报告展示数量限制施加到完整分析账本；
- 不使用标题相似度推断专利族；
- 不用单个超大 Prompt 消费一家公司的全部原始专利文本；
- 不实现角色化多 Agent 讨论；
- 不因为一条高阶结论失败而重跑检索或整家公司；
- 不承诺仅凭专利公开量即可代表真实市场份额或商业竞争力。

## 4. 集合与统计口径

定义：

- `R`：Provider 返回的原始 Hit 多重集；
- `P`：完成硬过滤和规范化公开号去重后的合格公开文本集合；
- `U`：按可靠 Family ID、申请号、公开号依次保守聚合后的分析单元集合；
- `C`：完成唯一主公司归属后的分析单元集合；
- `F`：至少具有可用标题或摘要片段的分析单元集合；
- `D`：已生成 `PatentDirectionRecord` 的完整方向账本；
- `M`：已分配到统一技术微簇的方向记录集合；
- `G`：已完成标签和摘要的统一技术分类；
- `A`：完成可选深度精读的代表专利集合。

必须满足：

```text
D ⊆ F ⊆ C = U
M = D
A ⊆ F
```

正常成功报告要求：

```text
D = F = U
M = D
```

若 Provider、详情抓取或文本质量导致 `F != U`，系统可以输出受限报告，但
必须记录：

- 缺失的分析单元；
- 缺失原因；
- `|F| / |U|`；
- 对公司和技术分类造成的偏差；
- 是否仍满足最低报告门槛。

默认计数单位为 `PATENT_FAMILY`。每个分析单元必须保存：

- `analysis_unit_id`；
- `representative_publication_number`；
- `member_publication_numbers`；
- `identity_basis`：`FAMILY_ID | APPLICATION_NUMBER | PUBLICATION_NUMBER`；
- `identity_conflicts`。

## 5. 输入契约

### 5.1 用户输入

```json
{
  "technology_direction": "可选技术方向",
  "competitors": [
    {
      "name": "公司主名称",
      "aliases": ["用户确认的别名"],
      "assignee_scope": "ENTITY"
    }
  ],
  "publication_start": "2025-01-01",
  "publication_end": "2025-12-31",
  "analysis_intent": "AUTO",
  "counting_unit": "PATENT_FAMILY",
  "time_bucket": "AUTO"
}
```

### 5.2 程序策略

以下策略由服务器配置，不要求普通用户填写：

```json
{
  "max_analysis_units": 1000,
  "direction_batch_size": 6,
  "direction_concurrency": 3,
  "embedding_batch_size": 32,
  "cluster_label_concurrency": 3,
  "company_profile_concurrency": 3,
  "display_category_limit": 12,
  "representatives_per_cluster": 6,
  "representatives_per_company_cluster": 3,
  "deep_read_limit": 20,
  "max_repair_rounds": 1,
  "minimum_direction_coverage": 1.0,
  "minimum_time_buckets_for_trend": 3,
  "minimum_units_for_trend": 4
}
```

外部检索预算和内部分析预算必须分开：

- `per_query_limit`：限制 Provider 单查询返回量；
- `max_analysis_units`：限制进入分析层的去重分析单元；
- `deep_read_limit`：只限制可选精读；
- 并发数只影响资源消耗，不得改变集合成员。

超出 `max_analysis_units` 时必须进入明确策略：

1. 请求用户缩小公司、技术或时间范围；
2. 用户批准受限分析，并在报告中冻结受限集合；
3. 进入离线大任务模式。

禁止静默截断后声称分析了完整集合。

## 6. 领域模型

### 6.1 `PatentDirectionRecord`

每个分析单元对应一条方向记录：

```json
{
  "direction_record_id": "DR-...",
  "analysis_unit_id": "AU-...",
  "representative_publication_number": "US...",
  "company_id": "CO-...",
  "publication_date": "2025-06-01",
  "time_bucket": "2025-Q2",
  "title": "原始标题",
  "technical_problem": "需要解决的技术问题",
  "solution_mechanism": "核心技术机制",
  "technical_object": "作用对象或关键组件",
  "application_scenarios": ["应用场景"],
  "normalized_keywords": ["标准化关键词"],
  "direction_summary": "面向聚类的短技术摘要",
  "confidence": 0.87,
  "evidence_refs": [
    {
      "evidence_id": "EV-DIR-...",
      "section_type": "ABSTRACT"
    }
  ],
  "extractor_version": "direction-record/v1",
  "content_hash": "..."
}
```

约束：

- `direction_summary` 建议不超过 600 个中文字符；
- 模型不得回显公司 ID、专利号和日期作为权威值；
- 程序使用任务输入回填上述字段；
- 每个语义字段必须至少有一个允许的 Evidence 引用；
- 抽取失败只影响当前分析单元，不得重跑整家公司。

### 6.2 `TechnologyMicroCluster`

技术微簇是跨公司统一分类的基础：

```json
{
  "cluster_id": "MC-...",
  "parent_cluster_id": null,
  "member_direction_record_ids": ["DR-1", "DR-7"],
  "centroid_ref": "VECTOR-...",
  "cohesion": 0.81,
  "cluster_size": 12,
  "is_noise": false,
  "algorithm_version": "technology-clustering/v1"
}
```

成员关系由程序和聚类算法所有。模型只能：

- 建议合并已存在的 Cluster ID；
- 建议拆分需要复核的 Cluster ID；
- 给 Cluster 命名和总结；
- 解释聚类内的共同技术机制。

模型不得直接返回专利号成员列表。

### 6.3 `TechnologyClusterLabel`

```json
{
  "cluster_id": "MC-...",
  "name": "GPU 动态缓存与内存调度",
  "summary": "该方向主要通过……",
  "keywords": ["GPU", "L1 缓存", "内存调度"],
  "technical_scope": "分类边界",
  "limitations": [],
  "representative_direction_record_ids": ["DR-1", "DR-7"]
}
```

程序必须验证所有引用的方向记录属于该 Cluster。

### 6.4 `CompanyDirectionLedger`

公司账本不是模型输出，而是统一技术分类按公司切片后的程序视图：

```json
{
  "company_id": "CO-...",
  "analysis_unit_count": 39,
  "cluster_slices": [
    {
      "cluster_id": "MC-...",
      "direction_record_ids": ["DR-1", "DR-9"],
      "unit_count": 8,
      "company_share": 0.2051,
      "time_bucket_counts": {
        "2025-Q1": 1,
        "2025-Q2": 2,
        "2025-Q3": 2,
        "2025-Q4": 3
      }
    }
  ]
}
```

所有数量、比例和时间桶由程序计算。

### 6.5 `CompanyTechnologyProfile`

公司画像消费紧凑的公司账本和类别标签：

```json
{
  "company_id": "CO-...",
  "display_name": "公司名称",
  "overall_summary": "公司技术组合摘要",
  "primary_cluster_ids": ["MC-1", "MC-3"],
  "differentiated_cluster_ids": ["MC-8"],
  "technology_directions": ["方向一", "方向二"],
  "limitations": [],
  "profile_version": "company-profile/v3"
}
```

完整成员保存在公司账本，不复制进画像文本 Schema。

### 6.6 `CompanyClusterTimeMetric`

```json
{
  "company_id": "CO-...",
  "cluster_id": "MC-...",
  "bucket": "2025-Q2",
  "unit_count": 5,
  "company_cluster_share": 0.25,
  "cluster_company_share": 0.40,
  "delta_from_previous": 2,
  "normalized_delta": 0.10,
  "metric_confidence": "SUFFICIENT"
}
```

模型不能修改 Metric。

### 6.7 `TrendCandidate`

程序从统一分类和时间指标生成有限候选：

```json
{
  "trend_candidate_id": "TDC-...",
  "candidate_type": "COMMON_DIRECTION",
  "cluster_ids": ["MC-3"],
  "company_ids": ["CO-A", "CO-B"],
  "metric_refs": ["METRIC-1", "METRIC-2"],
  "representative_packet_ids": ["EP-A", "EP-B"],
  "allowed_direction": "UNCERTAIN",
  "program_rationale_codes": ["MULTI_COMPANY", "SINGLE_TIME_BUCKET"]
}
```

候选类型至少包括：

- `COMMON_DIRECTION`；
- `DIFFERENTIATED_DIRECTION`；
- `TEMPORAL_CHANGE`；
- `PORTFOLIO_CONCENTRATION`；
- `CROSS_DOMAIN_INTERSECTION`。

### 6.8 `CrossCompanyTrendNarrative`

模型只对给定候选进行解释：

```json
{
  "trend_candidate_id": "TDC-...",
  "name": "趋势名称",
  "summary": "基于程序指标和代表证据的解释",
  "direction": "UNCERTAIN",
  "used_representative_packet_ids": ["EP-A", "EP-B"],
  "limitations": []
}
```

程序根据候选和 Evidence Packet 回填最终报告中的：

- `trend_id`；
- `company_ids`；
- `cluster_ids`；
- `publication_numbers`；
- `evidence_ids`；
- `time_basis`；
- 程序指标。

## 7. 统一技术分类

### 7.1 为什么必须先建统一分类

如果每家公司分别生成自己的类别，同一技术可能得到不同名称和边界，跨公司
比较只能依赖模型猜测。目标架构必须先对全部 `PatentDirectionRecord` 建立
统一技术分类，再按公司切片。

```text
全部方向记录
    → 全局技术微簇
    → 统一 Cluster Label
    → 公司切片
    → 公司画像与跨公司比较
```

### 7.2 聚类策略

首版采用可替换的 `TechnologyClusterer` 接口：

```text
fit(direction_records) -> micro_clusters
assign(new_records, frozen_taxonomy) -> memberships
```

推荐特征：

- `direction_summary` 向量；
- `technical_problem`；
- `solution_mechanism`；
- `technical_object`；
- 标准化关键词；
- 可选 IPC/CPC；
- 不把公司名称作为技术相似度特征。

推荐流程：

1. 对方向记录生成向量；
2. 以程序算法生成微簇和噪声记录；
3. 对低凝聚度簇执行有界复核；
4. 为每个簇选择中心、边界和高排名代表记录；
5. 模型基于代表记录命名和总结；
6. 程序验证 Label 引用和 Evidence；
7. 冻结 taxonomy version。

Embedding 不可用时可以使用词法和规范字段相似度降级，但必须标记
`LEXICAL_FALLBACK`，不得伪装为等价质量。

### 7.3 类别数量

完整微簇数量由数据决定，不设置 20 类硬上限。

报告展示使用：

- 主要方向：默认最多 12 个；
- 长尾方向：保留为可展开列表；
- 噪声记录：单独显示“未形成稳定簇”，保留原始方向记录；
- 禁止把所有长尾不可逆地合并成一个“其他技术方向”。

### 7.4 为什么不能只使用一个向量

整段文本生成一个向量只能表示总体语义接近，容易出现以下误合并：

- 两件专利都出现“人工智能”，但一个优化 GPU 缓存，另一个生成驾驶决策；
- 两件专利都属于无线通信，但一个处理核心网注册，另一个处理射频波束；
- 标题高度相似，但解决机制和技术对象不同；
- 摘要包含大量通用背景，掩盖真正的核心发明机制。

因此，v3 不直接对完整标题和摘要生成一个“万能向量”后按余弦距离聚类。
程序先从有证据的 Direction Record 中形成多个语义视图：

```text
problem_vector       ← technical_problem
mechanism_vector     ← solution_mechanism
object_vector        ← technical_object
scenario_vector      ← application_scenarios
recall_vector        ← problem + mechanism + object + keywords
```

其中：

- `recall_vector` 只用于快速找到可能相似的候选邻居；
- 其他字段向量用于精确计算两条 Direction Record 的技术相似度；
- 原始专利全文不直接进入聚类向量；
- 所有向量使用同一 Embedding Model、同一归一化规则和明确版本；
- 公司名称、公开日期、检索排名不得进入技术相似度向量。

### 7.5 混合相似度评分

两条方向记录 `a`、`b` 的首版相似度建议为：

```text
problem_similarity   = cosine(a.problem_vector, b.problem_vector)
mechanism_similarity = cosine(a.mechanism_vector, b.mechanism_vector)
object_similarity    = cosine(a.object_vector, b.object_vector)
scenario_similarity  = cosine(a.scenario_vector, b.scenario_vector)
keyword_similarity   = weighted_jaccard(a.keywords, b.keywords)
class_similarity     = ipc_cpc_hierarchy_similarity(a, b)

total_similarity =
    0.25 * problem_similarity
  + 0.35 * mechanism_similarity
  + 0.15 * object_similarity
  + 0.08 * scenario_similarity
  + 0.10 * keyword_similarity
  + 0.07 * class_similarity
```

权重含义：

- 解决机制最重要，决定专利“如何解决”；
- 技术问题次之，决定专利“为什么需要解决”；
- 技术对象避免把不同硬件、协议或系统误合并；
- 应用场景只做弱信号，避免同一底层技术因场景不同被完全拆开；
- 关键词用于补充具体部件和术语；
- IPC/CPC 只作为辅助先验，不能替代文本技术语义。

某字段缺失时，不把该字段记为 0，而是只在可用字段上重新归一化：

```text
available_score =
    Σ(weight_i * similarity_i * available_i)
    / Σ(weight_i * available_i)
```

初始权重只是工程基线，不是永久事实。必须使用标注的“同方向/不同方向”
专利对进行校准，以“错误合并成本高于错误拆分”为原则选择权重和阈值。

### 7.6 从向量到最终技术簇的完整流程

以一个包含 `N` 条 Direction Record 的 Run 为例：

#### 第一步：有界语义抽取

模型从标题、摘要和必要证据中抽取技术问题、解决机制、技术对象、场景和
关键词。程序校验 Evidence，并持久化 Direction Record。

#### 第二步：分别向量化

Embedding 服务分别为问题、机制、对象、场景和 Recall Text 生成向量。
向量写入 PostgreSQL/pgvector，并带：

- Embedding Model；
- 维度；
- 归一化方式；
- 输入字段 Hash；
- 生成时间。

#### 第三步：候选邻居召回

对每条记录使用 `recall_vector` 从 pgvector 取 Top-K 候选，例如 `K=20`。

这一步追求高召回：可能相似的记录尽量不要漏掉，但暂不直接决定聚类。
因此无需计算所有 `N²` 组合，也不会仅凭 Recall Vector 合并。

#### 第四步：混合重排

程序对候选记录对计算 7.5 的混合分数，并应用约束：

- 技术对象和机制均明显不相似时，即使总体文本相似也不得建立强边；
- IPC/CPC 缺失不直接惩罚；
- IPC/CPC 大类不同且对象、机制都弱时，应降低最大可信分；
- 同公司、不同公司不得影响技术相似度；
- 时间接近不得提高技术相似度。

建议首版分区：

```text
score >= 0.78       强相似边
0.68 <= score < 0.78 复核区
score < 0.68        不建立边
```

阈值必须通过评测校准，不得只根据单次真实 Run 调整。

#### 第五步：构建相似度图

每条 Direction Record 是一个节点，达到阈值的候选对形成带权边：

```text
DR-01 ─0.86─ DR-07
  │             │
 0.81          0.83
  │             │
DR-12 ─0.79─ DR-19
```

程序在加权图上执行确定性的社区发现或约束聚合，形成技术微簇。
实现必须通过 `TechnologyClusterer` 接口封装算法，避免业务层绑定某一个库。

聚类质量门至少包括：

- 簇内平均相似度；
- 簇内最低相似度；
- 簇大小；
- 边界记录比例；
- Noise 数量；
- 是否存在只有通用关键词、没有共同机制的簇。

禁止仅使用普通 Connected Components，因为一串弱边可能造成“链式误合并”。

#### 第六步：形成层次分类

叶子微簇保持较严格的技术机制一致性。例如：

```text
AI 计算硬件优化（父类）
├── GPU 动态缓存与共享内存调度（叶子微簇）
├── 矩阵乘法与算子转换加速（叶子微簇）
└── 多处理器同步与数据移动（叶子微簇）
```

父类用于报告导航，叶子簇保存真实成员关系。父类合并不得抹掉叶子簇。

#### 第七步：代表样本与模型命名

程序从每个簇选择中心记录、边界记录、不同公司和不同时间桶代表，构造
Evidence Packet。模型只为现有 Cluster ID 生成名称、摘要和分类边界。

#### 第八步：程序审计

程序检查：

- 每条 Direction Record 恰好属于一个叶子簇或 Noise；
- 模型引用的记录确实属于该簇；
- 所有摘要引用的 Evidence 合法；
- 统计使用完整成员，不只使用代表样本；
- 公司切片之和与完整技术簇成员一致。

### 7.7 六件专利示例

假设有六条方向记录：

| 记录 | 技术问题 | 解决机制 | 技术对象 |
|---|---|---|---|
| `DR-A` | GPU 缓存配置固定 | 动态调整 L1/共享内存 | GPU 缓存控制器 |
| `DR-B` | 混合负载内存利用率低 | 按任务元数据重配置缓存 | GPU 内存控制器 |
| `DR-C` | AI 向量运算效率低 | 转换为矩阵乘法 | AI 计算单元 |
| `DR-D` | 5G 注册重定向安全 | 派生安全上下文密钥 | 5G 核心网 AMF |
| `DR-E` | 无线下行传输不稳定 | 多径反馈和预编码 | 基站/终端 |
| `DR-F` | 折叠屏布局失配 | 按屏幕状态重排 UI | 折叠显示设备 |

程序可能得到：

```text
DR-A ↔ DR-B
mechanism 0.91, problem 0.84, object 0.88, keyword 0.72
total 0.86 → 强相似边

DR-A ↔ DR-C
mechanism 0.48, problem 0.61, object 0.65, keyword 0.30
total 0.54 → 不建立边

DR-D ↔ DR-E
都属于无线通信，但机制、对象和技术问题不同
total 0.57 → 不建立叶子簇强边，可以进入同一上层“无线通信”

DR-F ↔ 其他记录
total < 0.40 → 独立微簇或 Noise
```

最终结构可能是：

```text
计算硬件
├── MC-01 GPU 动态缓存与内存调度：DR-A、DR-B
└── MC-02 AI 矩阵算子加速：DR-C

无线通信
├── MC-03 核心网注册安全：DR-D
└── MC-04 下行预编码：DR-E

智能终端
└── MC-05 折叠屏界面适配：DR-F
```

这个例子说明：

- 向量用于发现可能相似的记录；
- 多字段权重用于确认是否真的相似；
- 图聚类形成严格叶子簇；
- 层次分类提供更宽的报告导航；
- 模型负责命名和解释，不负责成员关系。

## 8. 时间指标与趋势判定

### 8.1 程序指标

每个 `company_id × cluster_id × time_bucket` 至少计算：

- 分析单元数量；
- 占该公司当期专利组合比例；
- 占该技术簇当期公司分布比例；
- 相邻桶绝对变化；
- 相邻桶归一化变化；
- 有效桶数量；
- 缺失桶和样本置信度。

### 8.2 方向门槛

程序生成 `allowed_direction`：

- `UNCERTAIN`：有效桶不足或样本不足；
- `GROWING`：至少 3 个有效桶，近期连续上升且样本门槛通过；
- `DECLINING`：至少 3 个有效桶，近期连续下降且样本门槛通过；
- `SHIFTING`：公司内部技术组合占比发生显著结构变化；
- `EMERGING`：此前桶为零或极低，后续连续出现且满足绝对数量门槛；
- `STABLE`：多个有效桶变化处于稳定区间；
- `ACCELERATING`：增长斜率持续上升且样本充分。

模型输出必须是 `allowed_direction` 或更保守的 `UNCERTAIN`。

## 9. Evidence Packet

高阶 Agent 不直接读取全部专利正文，只读取程序构造的 Evidence Packet：

```json
{
  "packet_id": "EP-...",
  "scope_type": "COMPANY_CLUSTER",
  "scope_ids": ["CO-A", "MC-3"],
  "metrics": {},
  "representatives": [
    {
      "direction_record_id": "DR-...",
      "publication_number": "US...",
      "title": "...",
      "direction_summary": "...",
      "evidence_refs": ["EV-DIR-..."]
    }
  ],
  "excluded_count": 18,
  "selection_policy": "CENTROID_BOUNDARY_RECENCY/v1",
  "content_hash": "..."
}
```

代表样本应覆盖：

- 最接近簇中心的记录；
- 簇边界记录；
- 不同公司；
- 不同时间桶；
- 检索排名较高的记录；
- 可选异常记录。

Packet 必须记录未进入上下文的成员数量和选择规则。

## 10. 上下文系统

### 10.1 `LandscapeContextPack`

```text
POLICY
SCOPE
ALLOWED_SOURCE_IDS
PROGRAM_FACTS
PROGRAM_METRICS
REPRESENTATIVE_EVIDENCE
LIMITATIONS
OUTPUT_CONTRACT
```

每次模型调用必须持久化 Context Manifest：

- `context_id`；
- `agent_name`；
- `scope_hash`；
- `allowed_source_ids`；
- `selected_packet_ids`；
- `excluded_item_count`；
- `estimated_input_tokens`；
- `reserved_output_tokens`；
- `prompt_version`；
- `model`；
- `content_hash`。

不保存隐藏思维过程。

### 10.2 默认上下文预算

| Agent | 单次最大输入 | 单次最大输出 | 输入规模策略 |
|---|---:|---:|---|
| Direction Extractor | 24k tokens | 3k tokens | 每批 5～8 个分析单元 |
| Cluster Labeler | 16k tokens | 2k tokens | 单簇代表记录 |
| Company Profiler | 20k tokens | 3k tokens | 类别标签、指标和代表 Packet |
| Trend Narrator | 24k tokens | 4k tokens | 有限 Trend Candidate 与 Packet |
| Report Composer | 20k tokens | 4k tokens | 已验证结构化结论 |

预算必须可配置，并根据具体模型上下文窗口预留安全余量。

### 10.3 不变量

- 单次上下文不得随总专利数量无限增长；
- 数据量增加通过增加 Map 任务数量处理；
- 高阶 Agent 不接收原始完整专利集合；
- Output Schema 只包含模型真正所有的字段；
- 所有允许引用的 ID 必须显式列入 `ALLOWED_SOURCE_IDS`；
- 超预算内容必须进入 Context Manifest 的排除统计。

## 11. Agent 与程序职责

| 能力 | 模型 | 程序 |
|---|:---:|:---:|
| 别名建议 | ✓ | 校验、裁剪和绑定 |
| 技术问题/机制抽取 | ✓ | 输入范围、ID 回填、证据校验 |
| 向量生成 | Embedding Model | 批处理、缓存、维度校验 |
| 聚类成员关系 | 复核建议 | ✓ |
| 类别名称和摘要 | ✓ | Cluster ID 和成员校验 |
| 公司类别切片 |  | ✓ |
| 数量、比例、时间桶 |  | ✓ |
| 趋势候选生成 |  | ✓ |
| 趋势解释 | ✓ | 方向门槛和事实回填 |
| 专利号、公司 ID、Evidence ID |  | ✓ |
| 完整性审计 |  | ✓ |
| 修复路由 | 提供受限建议 | ✓ |

## 12. LangGraph 目标结构

```text
VALIDATE_SCOPE
  → PLAN_SEARCH
  → SEARCH_PUBLICATIONS
  → NORMALIZE_FILTER_ASSIGN
  → BUILD_DIRECTION_INPUTS
  → EXTRACT_DIRECTION_RECORDS        Send(analysis_unit_id / batch)
  → VERIFY_DIRECTION_COVERAGE
      ├─ REPAIR_DIRECTION_RECORDS
      ├─ LIMITED
      └─ PASS
  → EMBED_DIRECTION_RECORDS
  → BUILD_GLOBAL_TAXONOMY
  → LABEL_TECHNOLOGY_CLUSTERS        Send(cluster_id)
  → COMPUTE_COMPANY_TIME_METRICS
  → BUILD_COMPANY_PROFILES           Send(company_id)
  → BUILD_TREND_CANDIDATES
  → NARRATE_CROSS_COMPANY_TRENDS     Send(trend_candidate_id / bounded batch)
  → SELECT_DEEP_READ
  → VERIFY_ANALYSIS
      ├─ REPAIR_LABEL
      ├─ REPAIR_PROFILE
      ├─ REPAIR_TREND
      ├─ LIMITED
      └─ PASS
  → BUILD_REPORT
```

### 12.1 Graph 要求

- Map 任务必须具有稳定 `task_key`；
- 单任务成功后立即持久化，不等待整个 Fan-out；
- 重启后只恢复未成功任务；
- Retry 必须作用于最小失败单元；
- Search、Filter 和身份冻结后，语义层 Repair 不得重新扩大检索范围；
- 一个 Repair 类型最多执行一轮，除非用户显式批准；
- Deep Read 是完成后可选 Enrichment，不阻塞主趋势报告。

### 12.2 有界自主判断

允许 Supervisor 在程序策略范围内判断：

- 某方向记录是否因证据不足需要补读摘要或独立权利要求；
- 某聚类是否低凝聚度并需要复核；
- 是否应当降低为 `LIMITED`；
- 是否已有足够信息停止；
- 应选择哪些代表记录进入高阶 Context。

Supervisor 不得判断：

- 是否跳过合格专利；
- 是否扩大公司或时间范围；
- 是否修改公司归属；
- 是否改变统计值；
- 是否绕过 Evidence 和 Coverage Gate。

## 13. 持久化设计

建议新增表：

```text
landscape_direction_records
landscape_direction_record_evidence
landscape_direction_embeddings
landscape_technology_taxonomies
landscape_technology_microclusters
landscape_technology_cluster_members
landscape_technology_cluster_labels
landscape_company_direction_metrics
landscape_company_profiles_v3
landscape_trend_candidates
landscape_cross_company_trends_v3
landscape_evidence_packets
landscape_context_manifests
```

### 13.1 表级约束

- `landscape_direction_records`：
  `UNIQUE(run_id, analysis_unit_id)`；
- `landscape_technology_cluster_members`：
  `UNIQUE(run_id, taxonomy_id, direction_record_id)`；
- 每个 Direction Record 必须恰好属于一个叶子微簇或显式 Noise 集合；
- Metric 的主键：
  `(run_id, company_id, cluster_id, time_bucket)`；
- Trend Candidate 引用的 Cluster、Company、Metric 和 Packet 必须存在；
- Context Manifest 只保存引用和 Hash，不复制大段专利正文；
- 所有写入必须包含版本和 `content_hash`。

### 13.2 迁移策略

采用追加迁移，不原地重解释历史报告：

1. 新增 v3 表；
2. 新 Run 写 v3；
3. Report API 按 `schema_version` 读取 v2/v3；
4. 旧表保留只读兼容；
5. v3 稳定后再单独审查旧结构删除，不在本工作包中删除。

## 14. 输出契约

Report 3.0 顶层：

```json
{
  "schema_version": "landscape-report/3.0.0",
  "analysis_intent": "COMPETITIVE_TECHNOLOGY",
  "scope": {},
  "coverage": {},
  "taxonomy": {},
  "company_profiles": [],
  "company_time_metrics": [],
  "cross_company_analysis": {},
  "deep_read": {},
  "audit": {},
  "context_usage": {},
  "limitations": []
}
```

### 14.1 Coverage

至少返回：

- 原始 Hit 数；
- 合格公开文本数；
- 分析单元数；
- 方向记录成功/失败数；
- 聚类成员覆盖率；
- 公司归属覆盖率；
- Evidence 覆盖率；
- 时间桶分布；
- Provider 限制；
- 是否发生词法降级；
- 未进入模型上下文但保留在分析账本中的数量。

### 14.2 Taxonomy

展示：

- 统一技术类别；
- 父子层次；
- 完整成员数量；
- 公司分布；
- 时间分布；
- 代表专利；
- 长尾与 Noise；
- 分类版本和限制。

### 14.3 公司画像

公司画像必须使用公司显示名，不以内部 Company ID 作为主标题。每家公司显示：

- 专利分析单元总数；
- 主要技术方向；
- 差异化方向；
- 技术组合集中度；
- 各方向时间分布；
- 代表专利；
- 长尾方向；
- 限制。

### 14.4 跨公司分析

每条结论必须区分：

- `PORTFOLIO_OBSERVATION`；
- `CROSS_COMPANY_COMPARISON`；
- `TEMPORAL_TREND`。

时间证据不足时只能输出前两类。

### 14.5 专利号外部链接

报告、前端、Markdown 和可下载 Taxonomy 中出现的专利公开号必须支持点击
打开专利详情。

后端新增确定性的 `PatentLinkResolver`：

```text
resolve(publication_number, source_provider?) -> PatentExternalLink | None
```

默认可以使用配置化模板：

```text
https://patents.google.com/patent/{normalized_publication_number}
```

Report 中的专利引用统一返回：

```json
{
  "publication_number": "US20260123456A1",
  "external_url": "https://patents.google.com/patent/US20260123456A1",
  "external_provider": "GOOGLE_PATENTS"
}
```

安全和一致性要求：

- URL 由程序根据规范化公开号生成，模型不得返回 URL；
- 域名和 URL Template 必须来自服务端 Allowlist；
- 公开号未通过规范化校验时只显示文本，不生成链接；
- 前端使用 `target="_blank"` 时必须同时使用
  `rel="noopener noreferrer"`；
- Markdown 中使用标准链接；
- CSV 至少提供单独的 `external_url` 列；
- 公司画像、技术簇、趋势证据、代表专利和精读列表使用同一 Resolver；
- Provider 自带详情 URL 只有通过 Allowlist 和公开号一致性校验后才可使用；
- Link Resolver 失败不得阻塞报告生成。

## 15. 精读选样

精读从完整 Direction Ledger 和统一分类中确定性选择：

1. 每家公司至少一个代表；
2. 每个主要技术簇至少一个中心样本；
3. 重要时间桶至少一个样本；
4. 高价值检索排名样本；
5. 聚类边界或异常样本；
6. 剩余名额按综合分分配。

精读可以增强：

- 技术机制解释；
- 重点专利对比；
- 独立权利要求总结；
- 风险和价值分析。

精读不得反向改写：

- 合格集合；
- 公司归属；
- 统一分类成员关系；
- 程序时间指标。

若精读发现分类异常，只能创建显式 Review/Repair Proposal。

## 16. 故障、降级与恢复

| 故障 | 默认处理 |
|---|---|
| 单件方向抽取失败 | 重试该分析单元；仍失败则记录缺口 |
| Embedding 不可用 | 词法降级并标记限制 |
| 单簇 Label 失败 | 使用程序关键词生成临时标签 |
| 公司画像失败 | 使用结构化指标生成确定性摘要 |
| Trend Narrative 失败 | 保留程序 Trend Candidate，不生成解释文本 |
| Context 超预算 | 减少代表样本，不删除账本成员 |
| 模型返回未知 ID | 拒绝该输出，不重跑上游 |
| Evidence 引用错误 | 定向修复当前对象 |
| Worker 重启 | 从成功的最小任务继续 |

降级结果必须满足事实完整性，不得以更流畅的文本换取成员丢失。

## 17. 可观测性

每个 Run 必须记录：

- 各集合大小；
- 每种 Map 任务数量、成功、失败、重试；
- 每个 Agent 输入/输出 token；
- Context Pack 选择/排除数量；
- 模型调用耗时和费用；
- Embedding 批次数和缓存命中；
- 聚类数量、Noise 数、凝聚度分布；
- 每家公司类别和长尾数量；
- Trend Candidate 数、实际叙述数和拒绝原因；
- Repair 路径；
- 报告限制。

日志和 Debug API 不得返回 API Key、完整用户专利正文或模型隐藏推理。

## 18. 测试与评测

### 18.1 合成数据规模

| Case | 数据 | 验证目标 |
|---|---|---|
| `LAND-020` | 20 件、1 公司、3 技术簇 | 基础方向账本和分类 |
| `LAND-100` | 100 件、2 公司、15 技术簇 | 双公司统一分类与跨公司比较 |
| `LAND-200` | 200 件、5 公司、30 技术簇 | 长尾、Noise、上下文预算和恢复 |
| `LAND-1000` | 1000 件合成方向记录 | 纯分析层扩展性，不调用 Provider |
| `LAND-TIME-1` | 单时间桶 | 禁止强趋势 |
| `LAND-TIME-2` | 两个时间桶 | 仅前后差异 |
| `LAND-TIME-4` | 四个时间桶 | 程序趋势方向 |
| `LAND-FAIL` | 模型/Embedding/Worker 故障 | 定向重试和降级 |

### 18.2 硬性指标

| 指标 | 门槛 |
|---|---:|
| 合格分析单元身份覆盖 | 100% 或显式 LIMITED |
| Direction Record 重复 | 0 |
| Cluster Membership 重复 | 0 |
| 未知 Company/Patent/Evidence ID 被接受 | 0 |
| 程序统计被模型改写 | 0 |
| 单时间桶输出强趋势 | 0 |
| Context 超预算调用 | 0 |
| 重启后重复外部副作用 | 0 |
| 无来源的高阶结论 | 0 |
| 模型生成的外部专利 URL 被接受 | 0 |

### 18.3 质量指标

- Direction Record 字段证据支持率；
- 聚类 Coverage；
- 合成真值下的 Cluster Purity/NMI；
- 类别标签与代表记录一致率；
- 公司重点方向召回率；
- Trend Candidate 支持率；
- Unsupported Comparison Rate；
- 模型结构化输出首次通过率；
- 平均每件分析单元 token 和费用；
- 20/100/200 规模下的整体耗时；
- 单个对象 Repair 成功率。

### 18.4 上下文测试

必须验证：

- 中文、英文和混合专利文本；
- 极长摘要和权利要求；
- 1000 条方向记录仍无单次超预算 Prompt；
- 相同输入和版本生成稳定 Context Hash；
- 代表样本选择具有公司、时间和技术多样性；
- 排除项完整记录；
- 专利文本中的 Prompt Injection 不能改变 Agent 权限。
- 合法公开号生成 Allowlist 内的确定性链接；
- 非法公开号、协议注入和非 Allowlist 域名不生成可点击链接；
- JSON、HTML、Markdown 和 CSV 中的专利链接保持一致。

## 19. 验收门

v3 进入默认路径前必须满足：

1. PostgreSQL v3 表和约束通过集成测试；
2. Direction Record 全量账本可恢复；
3. 统一技术分类不依赖公司名称特征；
4. 报告展示限制不影响完整成员关系；
5. 公司画像不再拼接全部类别摘要；
6. Cross-company Agent 不直接生成程序所有的 ID；
7. 单次模型上下文均受 Context Manifest 预算控制；
8. `LAND-020/100/200/1000` 全部通过硬性指标；
9. v2 历史报告仍可读取；
10. 真实 100 件双公司 Run 在不丢失成员的情况下完成；
11. 所有失败都能定位到最小任务键；
12. 开发记录、迁移说明和报告契约同步完成。
13. 所有面向用户的专利号通过统一 Link Resolver 提供安全超链接。

## 20. 实施工作包与提交边界

每个工作包必须独立提交，不得跨包顺手重构。

### WP-0：冻结契约和 Fixture

- 新增 v3 Schema 测试；
- 新增 `LAND-020/100/200/1000` 合成 Fixture；
- 新增 Context Budget 测试；
- 不修改生产路径。

建议提交：

```text
test(landscape): freeze scalable analysis contracts
```

### WP-1：Direction Record 与持久化

- 新增 Direction Record Schema；
- 新增 PostgreSQL 表和 Repository；
- 从标题、摘要、Snippet 构建有界输入；
- 新增 Evidence 校验和恢复。

建议提交：

```text
feat(landscape): persist bounded patent direction records
```

### WP-2：Context Pack 与预算

- 新增 `LandscapeContextPack`；
- 接入 token 估算；
- 持久化 Context Manifest；
- 超预算 fail-closed。

建议提交：

```text
feat(landscape): add budgeted context packs
```

### WP-3：统一技术分类

- 新增 Embedding/词法降级接口；
- 新增全局微簇和成员表；
- 新增 Cluster Label Agent；
- 保留 Noise 和长尾。

建议拆分提交：

```text
feat(landscape): build global technology taxonomy
feat(landscape): label bounded technology clusters
```

### WP-4：公司切片与时间指标

- 从统一分类生成公司账本；
- 计算公司/类别/时间桶指标；
- 冻结趋势方向门槛。

建议提交：

```text
feat(landscape): compute company direction metrics
```

### WP-5：公司画像

- 使用紧凑账本和 Evidence Packet；
- 模型只生成 Narrative；
- 程序回填显示名和方向引用；
- 提供确定性降级摘要。

建议提交：

```text
feat(landscape): build compact company portfolio profiles
```

### WP-6：趋势候选与叙述

- 程序生成 Trend Candidate；
- 模型引用 Candidate/Packet ID；
- 程序回填公司、专利、证据和时间指标；
- 加入 Unsupported Comparison 校验。

建议拆分提交：

```text
feat(landscape): generate deterministic trend candidates
feat(landscape): narrate evidence-bound company trends
```

### WP-7：Graph、Repair 和 Report 3.0

- 接入动态 Map；
- 接入最小任务恢复；
- 接入定向 Repair；
- 新增 Report 3.0 和前端展示；
- 新增统一 `PatentLinkResolver`，覆盖报告、HTML、Markdown、CSV 和
  Taxonomy 下载；
- 保持 v2 历史读取。

建议拆分提交：

```text
feat(landscape): route scalable landscape graph
feat(landscape): publish landscape report v3
```

### WP-8：真实验证与性能评测

- 运行 20/100/200 合成全链路；
- 运行 100 件真实双公司场景；
- 记录 token、耗时、重试和限制；
- 更新本规格实际实现状态和开发记录。

建议提交：

```text
test(landscape): validate scalable trend pipeline
```

## 21. Coding Agent 强制规则

- 开始工作包前完整阅读本文件；
- 一次只实现一个可验收工作包；
- 先提交失败测试，再实现生产代码；
- 使用 PostgreSQL 作为唯一事实源；
- 不把新的 durable 状态写回进程内缓存作为唯一来源；
- 不让模型成为 ID、成员关系或统计值的权威；
- 不为了通过 Schema 删除分析成员；
- 不使用放宽验证替代正确的数据分层；
- 每个新 Agent 必须声明输入预算、输出预算和允许引用 ID；
- 每个 Map 任务必须具备稳定任务键和幂等写入；
- 每个提交必须更新测试和 `CHANGELOG.md`；
- 不提交 API Key、真实用户专利正文或完整模型原始响应；
- 发现实现需要偏离本规格时，先修改规格并提交审查。

## 22. 冻结决策

以下决策在本规格审查通过后冻结：

1. 先构建跨公司的统一技术分类，再生成公司切片；
2. Direction Record 是全量趋势分析的最小语义单元；
3. 全量账本与报告展示分类分离；
4. 模型不直接维护专利、公司和证据身份；
5. 时间指标由程序计算；
6. Trend Agent 消费有限候选，不消费全部专利原文；
7. Deep Read 是可选 Enrichment；
8. 单次上下文必须有硬预算；
9. Retry/Repair 作用于最小失败对象；
10. v3 使用追加迁移并保持 v2 只读兼容。
11. 专利外部链接由程序和 Allowlist 所有，模型不能生成链接。

## 23. 审查问题

开始开发前只需确认以下产品选择：

1. 默认计数单位是否冻结为 Patent Family；
2. `COMPANY_PORTFOLIO` 是否允许在没有技术方向时默认运行；
3. 默认报告主要方向展示 12 个是否合适；
4. Embedding 是否作为生产必需能力，还是允许长期词法降级；
5. 真实部署期望的最大分析单元是 200、500 还是 1000；
6. 时间趋势最低要求是 3 个季度，还是允许月度桶；
7. Report 3.0 是否需要同时输出机器可读 Taxonomy 下载文件。

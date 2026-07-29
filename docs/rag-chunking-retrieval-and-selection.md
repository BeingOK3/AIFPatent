# AIFPatent RAG：Chunk、动态装载与精读专利选择

本文回答四个具体问题：

1. 专利正文如何切成 Chunk；
2. 检索时怎样动态装载 RAG 上下文；
3. 多篇精读专利是分开加载还是一起加载；
4. 面对多条检索式返回的大量候选，哪些专利进入全文精读。

## 1. 一张总览图

~~~text
用户 IDEA
  → 解析 F1..Fn 技术特征
  → 8–16 条中英文检索式 × 多个 Provider
  → 合并、去重、摘要筛选
  → 选出 10–40 件（取决于模式）全文精读候选
  → 抓全文，要求至少有摘要和独立权利要求
  → 写不可变 Corpus Version
  → Chunk：摘要 / 每项权利要求 / 说明书段落
  → 每个 Feature × 每个精读专利版本检索 Chunk
  → 每篇精读专利各自组装一份 Context
  → 并行但彼此独立地交给模型分析
  → C1..Cn 引用校验后进入报告
~~~

最重要的结论：

- 多检索式找来的专利会先合并和筛选，**不是全部送去精读**；
- 精读专利的 RAG Context 是**按专利分开的**，一篇专利一份 Context；
- 但对该专利的 Context，会汇集所有必要 Feature 的检索结果，而不是为每个 Feature 单独发一次模型请求；
- 每篇精读专利的摘要和独立权利要求会被强制放入 Context，不完全依赖相关性排序。

## 2. Chunk 是如何切分的

Chunk 代码在 backend/idea/chunks.py，当前版本名为 claims-paragraphs-v2。切分规则是结构感知的，不是每 500 或 1,000 字机械切一次。

| 专利部分 | 当前 Chunk 规则 | 生成的 section_label |
| --- | --- | --- |
| 摘要 | 整个摘要一个 Chunk | abstract |
| 权利要求 | 能识别编号时，每一项权利要求一个 Chunk | claim-1、claim-2…… |
| 权利要求 | 无法可靠识别编号时，全部权利要求作为一个 Chunk | claims |
| 说明书 | 以空行为分段，每个非空段一个 Chunk | paragraph-1、paragraph-2…… |

每个 Chunk 保存以下元数据：

~~~text
chunk_id
version_id
publication_number
section_type / section_label
claim_number / claim_kind / parent_claim_numbers
start_offset / end_offset
text / text_hash
token_count / chunker_version
~~~

### 2.1 为什么这样切

摘要适合快速理解技术主题；权利要求是法律和技术比对的核心，尤其独立权利要求；说明书段落适合解释实现细节和技术效果。

例如一篇专利可能被拆为：

~~~text
CH-01  abstract
CH-02  claim-1，independent
CH-03  claim-2，dependent，parent = claim-1
CH-04  claim-3，dependent，parent = claim-1
CH-05  paragraph-1
CH-06  paragraph-2
...
~~~

这样的切分允许系统回答“F3 在权利要求 1 是否出现”“具体实现细节在说明书哪段”，并能将引用精确落到段落/权利要求。

### 2.2 权利要求如何识别独立/从属关系

系统尝试从权利要求的编号、文本中对其他权利要求的引用识别父关系：

- 没有父权利要求的 Claim，被标为 independent；
- 引用了其他 Claim 的，被标为 dependent；
- Claim 1 也被视为独立权利要求的强信号。

对一次 IDEA 精读，抓到的专利若没有摘要，或者没有可识别的独立权利要求，会被全文证据完整性检查拒绝，而不是带着不完整的核心证据继续分析。

### 2.3 当前 Chunk 设置的优点与限制

优点：

- 保留了专利文本原始结构；
- 每个 Claim 单独可检索；
- 可沿从属 Claim 找回父 Claim；
- Chunk ID、文本哈希和版本号稳定，利于 Citation 回查。

限制：

- 很长的一项独立权利要求仍是一个 Chunk，可能占很多 token；
- 说明书只按空行切，网页抓取没有空行或一段特别长时，粒度不理想；
- 当前没有章节标题窗口、相邻段合并、语义切分或跨语言对齐；
- token_count 目前用按空格分词的近似值，对中文不是精确模型 token 数。

这些限制正是后续可优化 Chunk 的方向，但不能为了“切得更碎”而破坏权利要求完整性和可引用性。

## 3. 多条检索式得到大量专利后，如何选精读对象

精读选择发生在 RAG 之前。先解决“哪些专利值得抓全文”，再解决“全文中哪些段值得给模型”。

### 3.1 候选合并和去重

查询规划器通常产生 8–16 条中英文检索式。每条查询会发给可用的 Provider。返回结果先按以下身份键合并：

~~~text
publication number
application number
family ID
没有以上 ID 时才使用 URL
~~~

因此同一件专利即使被多条查询、多个 Provider 命中，也会变成一条 MergedHit；它仍保留 found_by、query_ids、来源 URL 和原始 Provider 数据，便于审计。

当前 merge 的目的主要是消除同一公开文本的重复。它会保留 possible_family_keys 用于提示可能同族，但 README 也说明“专利族变体增强”仍未完成；不要把当前去重理解为完整的同族去重策略。

### 3.2 摘要级相关性筛选

合并后的候选最多保留到 candidate_max，然后根据标题与 snippet 做确定性相关性打分：

1. 解析搜索计划中的概念组、中文/英文词、广义词；
2. 检查术语在标题与摘要片段中的匹配；
3. 计算概念组覆盖度、匹配词数量和标题命中加分；
4. 过滤评审日期之后公开的文献；
5. 排除没有可规范化公开号的候选。

默认阈值为 relevance_score ≥ 0.15。注意：这还是摘要级筛选，不是 RAG Chunk 级检索，也不是 LLM 重新判断相关性。

### 3.3 预算如何决定精读数量

搜索模式定义在 config/ai4patent.json：

| 模式 | 候选上限 candidate_max | 精读最少 deep_review_min | 精读最多 deep_review_max |
| --- | ---: | ---: | ---: |
| quick | 60 | 10 | 10 |
| standard（默认） | 200 | 10 | 20 |
| deep | 400 | 20 | 40 |

项目还根据 IDEA 的范围宽度自动选精读目标数：

- narrow：使用 deep_review_min；
- medium：取 min 与 max 的中间值；
- broad：使用 deep_review_max。

因此默认 standard 模式下：

~~~text
范围窄  → 目标精读 10 篇
范围中  → 目标精读 15 篇
范围宽  → 目标精读 20 篇
~~~

用户也能在 API 中覆盖 candidate_max、deep_review_min、deep_review_max，但存在下限和相互关系校验。

### 3.4 选择和补位规则

选择顺序为：

~~~text
所有合格候选
  → 按日期合格、摘要相关性、公开号可识别过滤
  → 取前 deep_review_target 篇
  → 若少于 deep_review_min，则从日期合格且公开号可识别的剩余候选补位
~~~

补位候选可能低于相关性阈值。系统会标记 LOW_CONFIDENCE_RECALL_BACKFILL，明确说明“这是为高召回而补的全文核验候选，不能仅凭摘要进入最终结论”。

然后系统抓取选中的完整专利。抓取失败或缺少摘要/独立权利要求时，会从同轮合格候补中继续抓取补位，直到恢复原本目标数或候补耗尽。若最终成功抓到的全文数量低于 deep_review_min，会写入 DEEP_REVIEW_FETCHED_BELOW_MINIMUM limitation，后续结论必须明确降级。

这回答了“那么多检索式生成的专利如何选取”：**先按身份去重，再按摘要术语覆盖和日期做确定性筛选，在固定预算内选前 N 篇，必要时为召回和抓取失败补位。**

## 4. 对精读专利是一起加载，还是分别加载

### 4.1 首次报告：检索是 Feature × Version，模型 Context 是 Version 级

假设本次有 3 个必要 Feature，成功抓到并精读 15 篇专利：

~~~text
检索次数 = 3 个 Feature × 15 个冻结专利 Version = 45 个检索请求
~~~

每一条检索请求都严格只允许在一篇专利的一个 Version 内找 Chunk：

~~~text
F3 × CN123456A 的 version
  → 只能返回 CN123456A 该 version 的 Chunk
~~~

这是为了让“这件专利是否披露 F3”的判断不被别的专利混入。

但是模型调用不是 45 次。系统随后按 document_id 聚合：

~~~text
CN123456A：
  F1 命中 Chunk
  + F2 命中 Chunk
  + F3 命中 Chunk
  + 强制摘要
  + 强制独立 Claim
  → 这一篇专利的一份 Context
  → 一次 patent-document-analyzer 调用

CN234567B：
  同样独立组装另一份 Context
  → 另一次模型调用
~~~

所以答案是：

> 首次报告的精读专利**分开加载、分开让模型判断**；一篇专利一个 Context。但该 Context 合并了所有必要 Feature 对这篇专利的相关证据。

代码还检查一份 RAG Context 只能包含一个公开号；若一个 Context 混入两件专利，会直接报错。多个专利分析可以并发执行，但并发不是把它们拼进同一个 prompt。

### 4.2 为什么不把 15 篇精读专利一起塞给模型

如果一起加载，模型必须在很长的上下文中同时做三件难事：

- 分清每段到底属于哪件专利；
- 对每个 Feature 和每件专利建立矩阵；
- 不把 A 专利的证据误引用给 B 专利。

项目选择“逐件精读”，将问题变成：

~~~text
只问：CN123456A 是否披露 F1..Fn？
只给：CN123456A 的证据 Chunk。
~~~

这大幅降低跨文献串证风险。跨专利的新颖性结论随后由程序读取每件专利的已验证映射来决定，不让模型自行混合证据。

## 5. 首次报告 RAG 怎样动态装载

“动态装载”不是把所有 Chunk 预先塞进模型，也不是按用户滚动页面才加载；它是指每次 Run、每篇精读文献、按当前 Feature 和当前冻结版本即时选取少量证据 Chunk。

完整过程：

~~~text
NORMALIZE_AND_FETCH
  → 全文写 Corpus、生成 Chunk、记录冻结 snapshot

ANALYZE_DOCUMENTS
  → 对 Feature × Version 检索
  → 选出该版本的候选 Chunk
  → 强制加摘要和独立 Claim
  → 按 token 预算装入 Context
  → 调用模型
~~~

ContextAssembler 的动态预算算法：

1. 先计算 system prompt 和 question 占用；
2. 按既定证据顺序逐个尝试放入 Chunk；
3. 能放入就分配 C1、C2……并记录 selected_chunks；
4. 超预算则记录 excluded_chunks，原因为 INPUT_BUDGET；
5. 如果一个 Chunk 都放不进，直接失败；
6. 如果任何强制摘要/独立 Claim 因预算被排除，首次报告也直接失败。

每份 Context 还记录：

~~~text
run_id、context_id、prompt_version、retriever_version
corpus_snapshot_hash、allowed_version_ids
全部 messages
C# 对应的 Chunk、Version、公开号、偏移、text_hash
未选 Chunk、原因、预算与 limitations
~~~

这使“动态加载”仍然可回放：以后可以准确知道当时为某篇专利装进模型的是哪些段，而不是只知道最终报告。

## 6. 追问时如何动态装载

追问的 Context 组织方式不同：可以覆盖多篇用户选择的冻结专利，但仍只加载本轮检索到的少量 Chunk。

流程：

~~~text
用户问题
  → 追问规划器生成查询改写和选定公开号
  → 每条改写仅在冻结 version allowlist 内检索
  → 合并多个检索结果、去重
  → 尽量确保每个选择的 version 有至少一段证据
  → 一个追问 Context，含多篇专利的 C1..Cn
  → 回答器生成本轮回答
~~~

这里与首次报告不同：

| 场景 | Context 中可否有多篇专利 | 原因 |
| --- | --- | --- |
| 首次报告逐件精读 | 不可以 | 目标是生成“单篇专利 × 每个 Feature”的清晰映射 |
| 追问 | 可以 | 用户问题常需要比较多篇已经冻结的专利 |

即使追问 Context 含多篇专利，系统仍通过 version allowlist、每篇最多 Chunk 数、同 section_label 上限、去重和版本多样性控制上下文。

## 7. 一个简化数字例子

假设 standard 模式、范围 medium、4 个 Feature：

~~~text
候选上限：200
精读目标：15
成功抓到全文：14（1 篇失败）
每篇平均 Chunk：35
总 Chunk：约 490
~~~

首次报告不会把 490 个 Chunk 发给模型。它会：

~~~text
4 Feature × 14 Version = 56 次受范围限制检索
每篇专利：
  汇总本篇的命中 + 摘要 + 独立 Claim
  经预算后可能装入 8–20 个 Chunk
  → 14 个独立模型精读请求
~~~

之后，程序根据这 14 份独立、带 Citation 的映射做整体新颖性/创造性流程。

## 8. 当前设计的优点和取舍

优点：

- 多查询、多 Provider 的候选不会重复抓取；
- 精读数量有预算，成本可控；
- 摘要筛选偏高召回，低分候选可补位；
- 单篇 Context 避免跨专利串证；
- 强制摘要和独立 Claim，避免检索只命中边缘段落；
- 每次动态装载的 Context 有 hash、版本和 Citation 可审计。

取舍和不足：

- 精读初筛主要看标题/snippet 的术语覆盖，尚非语义 rerank；
- 默认 LEXICAL_ONLY，跨语言和同义改写召回有局限；
- 超长 Claim 或长说明书段可能消耗太多 Context；
- 强制摘要/独立 Claim 可能挤掉更相关的说明书细节；
- 首次报告把每篇专利分开分析，降低串证风险，但模型不能在同一调用内做跨文献比较；
- Landscape 目前没有完全接入这套 Corpus Chunk RAG。

## 9. 最简回答

- **如何切分？** 摘要整体一块、每项可识别权利要求一块、说明书按空行段落一块，并保存 Claim 层级和原文偏移。
- **如何动态装载？** 每个 Run 在抓取后按 Feature × 冻结 Version 检索少量 Chunk，再按 token 预算临时组装并持久化 Context。
- **精读专利一起还是分开？** 首次报告分开：一篇专利一份 Context、一次模型分析；该 Context 合并全部 Feature 对该专利的证据。追问可以在一个 Context 中比较多篇已冻结专利。
- **精读专利如何选？** 多检索式结果先按公开号/申请号/同族等标识合并，再做日期和标题/摘要术语相关性筛选，按范围和模式的预算取前 N 篇；不足或抓取失败时用候补补位。

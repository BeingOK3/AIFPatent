# Landscape V4 开发变更记录

本文件按时间记录 `feature/scalable-landscape-v3` 分支上的开发、测试与修复过程，
与 Git 提交一一对应，便于审计与追溯。

## 2026-08-04 会话

### 1. 测试环境与基线

- 在服务器 `124.223.158.48`（/home/ubuntu/AI4Patent/AIFPatent）按 README 搭建测试
  环境：`backend/.venv` + `pip install -r backend/requirements.txt`。
- 首次完整运行 landscape 测试：264 个用例，4 个失败（均为压力测试耗时断言）、
  3 个跳过（真实集成需环境变量开关）。

### 2. 性能修复：分类候选预计算（commit `1be17b5`）

- 问题：`ClassificationMatchingService._candidate_leaves` 对每件专利重复扫描整棵
  分类树（O(件数×节点数)，实测 500 件约 22 秒），导致 LAND-500/LAND-1000 压力
  测试超过耗时门槛。
- 修复：一次性预计算「一级分类 → 叶子」映射（`_leaf_parent_pairs`），候选召回变为
  O(件数×叶子数)，保持原有确定顺序；分类+压力测试从 22s 降至 0.6s。
- 回归：新增 `test_candidate_leaves_are_scoped_deterministic_and_complete`。

### 3. DeepSeek 真实模型集成测试（通过）

- 使用 `deepseek-v4-flash`（base_url `https://api.deepseek.com`），密钥仅作临时
  环境变量，不落盘、不入库、不进 Git。
- `AIFPATENT_RUN_SCOPE_LLM_INTEGRATION=1` 下运行
  `test_landscape_scope_expansion`：10 个用例全部通过，包括「华为」公司名称扩展与
  「数据中心液冷冷板微通道歧管」技术词扩展（中英文双语、关系类型与理由齐全）。

### 4. 真实 PostgreSQL v4 Run 集成测试（通过）

- `AIFPATENT_RUN_SCOPE_INTEGRATION=1` 下运行
  `LandscapeV4RunPostgreSQLIntegrationTests`：确认范围 → 创建 Run → 可回放、
  回滚干净。

### 5. 端到端真实 Run（第一次，发现并修复崩溃 Bug）

- 通过 API 完成：创建范围草稿 → DeepSeek 扩展（公司+技术词）→ 用户审查
  （激活 6 个公司名称、5 个技术词，其余 EXCLUDED）→ 确认 → 创建 Run →
  真实 SerpAPI 检索（6 个查询、估计 759 条、冻结 273 件公开、家族合并 267 个
  分析单元）→ 273 件摘要全部获取。
- 发现 1：公司扩展首次调用瞬时失败，系统按文档降级保留原始输入并记录
  `COMPANY_EXPANSION_FAILED`；重试即成功，证明降级路径正确。
- 发现 2（严重）：方向抽取完成后，`MATCH_TAXONOMY` 阶段因
  `BatchPackingError` 失败导致整次 Run FAILED。根因：某件专利方向记录的一级
  候选为空，分类器回退到全部 632 个叶子，超过批次档案上限
  `max_taxonomy_candidates=500`；有界复核轮的候选集同样可能超限。
- 修复：候选生成处按批次档案上限做确定性截断（`_cap_candidates`），空候选回退
  与复核轮均受同一上限约束（commit `7dda5fa`）。用同一批 260 条真实方向记录 +
  真实 DeepSeek 离线复跑分类：260 件全部进入互斥终态
  （CLASSIFIED 250 / UNRESOLVED 8 / OTHERS 2），不再崩溃。

### 6. 端到端真实 Run（第二次，验证修复）

- 复用已确认范围 `SCR-c6d742d9cc8ec150` 创建 Run `LRN-b32de73ddfd26301`，
  最终状态 `COMPLETED_WITH_LIMITATIONS`，13 个阶段全部成功：
  - 真实 SerpAPI 检索：6 个查询、原始命中 622、冻结 Publication 273、
    家族合并 Analysis Unit 267；分页超过第一页（单查询最多 3 页），停止原因
    真实记录（PROVIDER_HARD_LIMIT / QUERY_EXHAUSTED）；
  - 摘要获取 273/273 成功；
  - 方向抽取 267/267（7 件 UNRESOLVED，有明确原因）；
  - Taxonomy 分类 267/267：CLASSIFIED 249、OTHERS 2、UNRESOLVED 16 ——
    修复后不再出现 BatchPackingError；
  - Others 2/2、指标 267/267、趋势候选 61/61、代表专利 45/45、
    报告 1/1、校验 1/1；
  - 报告可见限制：DIRECTION_UNRESOLVED(7) + CLASSIFICATION_UNRESOLVED(16)，
    与文档“限制必须用户可见”一致；
  - 趋势遵循规格：样本不足时仅输出当前布局/观察，不虚构增长下降。
- 密钥安全：DeepSeek 密钥仅作临时环境变量/进程内存租约使用，未入库、未进
  报告、未进 Git（已扫描确认 0 命中）。

### 7. 性能优化与复测（commit `28e95ce`）

背景实测（探针，2026-08-04）：
- DeepSeek v4-flash 在结构化输出时会默认生成大量 `reasoning_content`
  （8192 上限探针中思考内容占 5693 字符），既占满输出预算又拖慢生成；
  实测 `thinking: {"type": "disabled"}` 与 `reasoning_effort: "none"` 均可使
  思考内容归零（完成 token 24→6，耗时减半），`max_tokens=20000` 亦被接受。
- 并发探针：16 个并发小请求无 429，延迟持平；应用此前并发=3，远低于
  DeepSeek 可承受上限；本地 CPU/内存几乎空闲（模型阶段 CPU 0.1%~9%、
  内存 ~150MB），瓶颈完全在模型 API 生成与并发限制。

改动：
- `model_client.py`：DeepSeek（api.deepseek.com）结构化输出同样关闭思考，
  与既有 Ark 路径共用 `thinking: {"type": "disabled"}`；
- `runtime.py`：新增 `AIFPATENT_MODEL_MAX_CONCURRENCY`（默认继承
  document_agent_concurrency，上限 16），默认 TPM 200K→1M；
- `rag.env` / `compose.yml`：并发 8、RPM 120、TPM 100 万透传进容器。

复测（复用同一确认范围，Run `LRN-0605a39a27796c7b`）：

| 阶段 | 优化前 | 优化后 |
|---|---:|---:|
| 方向抽取（267 件） | ~1037s | **71s** |
| 分类（267 件） | ~830s | **83s** |
| 整次 Run | ~30min | **~3min** |

- Unresolved 由 7+16 降至 4+6（分类覆盖 256、Others 5、Unresolved 6），
  正确性无回退；267 个离线测试用例全部通过。

### 8. 质量 A/B 与并发上限复测（2026-08-04）

同一批真实专利方向抽取 A/B：

| 场景 | 成功 | 平均置信度 | 耗时 |
|---|---:|---:|---:|
| 关思考（并发 4，20 件） | 20/20 | 0.927 | 17.6s |
| 开思考（并发 4，20 件） | 20/20 | 0.801 | 97.0s |

- 关思考未降低质量：全部成功、置信度更高、抽样摘要同样合理且更简洁；
  机制上思考 token 会挤占输出预算，增加截断/校验失败风险。

并发上限（关思考，88 件方向抽取）：

| 并发 | 耗时 | 结果 |
|---|---:|---|
| 8 | 54.6s | 88/88 |
| 16 | 20.7s | 88/88，无 429 |

- 服务器 `rag.env` 并发 8→16（RPM 240），完整端到端复测
  （Run `LRN-02f1cd5f34a9e9c1`）稳定完成：方向抽取 52s、分类 84s、
  整 Run ~3min，Unresolved 4+7，无 429/无失败。

## 2026-08-05 — 真实运行验收、宏观总结、检索截断与前端美化

### 9. 三模式真实运行状态（124.223.158.48）

- COMPANY_AND_TECHNOLOGY：历史 3 次成功（LRN-02f1cd5f、LRN-0605a39、LRN-b32de73）。
- TECHNOLOGY_ONLY：本次真实跑通（LRN-0c325c02ad11bf53，AI 服务器液冷，2025-08-01~2026-07-31），
  950 冻结公开文本 → 897 分析单元 → 660 趋势候选 → 98 代表专利，状态 COMPLETED_WITH_LIMITATIONS。
- COMPANY_ONLY：本次真实运行中（LRN-b9d4f2cc933fd665，英伟达/华为），限额下冻结 1980 件。

### 10. 真实运行暴露并修复的缺陷

- 指标立方哈希不一致：Python 码点序与 PostgreSQL 默认 collation 排序不同，同一方向下
  `ORG-UNKNOWN`（大写）与已知机构（小写 hex）混排时顺序翻转，写库重建后 `cube_hash` 校验失败，
  仅技术模式（大量 UNKNOWN 机构）必现。修复：指标 cell 读取按 `COLLATE "C"` 排序，
  与构建端 Python 顺序一致（提交 5b3a50a）。
- 检索页 CHECK 约束：新增 `MAX_PAGES` 停止原因被 `landscape_v4_search_pages_stop_reason_check`
  拒绝（迁移 103 放开，提交 fe409d2）。

### 11. 召回量分析与截断上限

- 根因：窗口内检索量取决于 Provider 报告的 total_results；公司模式别名查询越多、窗口越长，
  total 求和越大。仅公司 2 家、30 条查询、1 年窗口，估算 269,962 件 / 2710 页 / 1350 分片。
- 修复：`AIFPATENT_LANDSCAPE_MAX_PAGES_PER_QUERY`（默认 8）与
  `AIFPATENT_LANDSCAPE_MAX_FROZEN_PUBLICATIONS`（默认 3000）：取页到上限后追加
  `MAX_PAGES` 终止页，冻结按公开日确定性截断并记录 `truncated_count`，规模估算同步封顶，
  限制以 `FROZEN_PUBLICATION_CAP`/`MAX_PAGES` 如实进入报告（提交 d35e8bc）。
- 限额后仅公司模式估算 2,888 / 37 页 / 15 分片，冻结 1,980 件。

### 12. 报告质量与前端

- 报告 4.1.0 新增 `macro_summary`（程序化宏观总结：总体规模、时间脉冲、Top 方向/机构、
  变化分布与一句话叙述，样本不足不虚构），前端顶部宏观总结卡片（提交 5d2195f）。
- 生成提速：模型阶段 16 并发不变；详情抓取并发参数化 `AIFPATENT_LANDSCAPE_FETCH_CONCURRENCY`
  （默认 8，上限 64，提交 9f5c453），待重建后 16 并发复测。
- 前端美化：报告头部、趋势迷你条形图、变化类型彩色徽章、趋势/专利大表分页折叠（提交 dcfed60）。

### 待办

- COMPANY_ONLY Run 完成后验证报告与截断限制；重建容器（含抓取并发 16 与前端）后复测提速；
- 真实三模式验收清单全部打勾后，向用户交付运行证据。

### 13. 仅公司真实运行发现：SerpAPI 详情抓取配额瓶颈

- COMPANY_ONLY Run（LRN-b9d4f2cc933fd665）完成：1980 冻结 / 1974 单元 / 231 方向 / 3 机构，
  报告 4.1.0，模式视图 COMPANY_ONLY=机构主轴、方向次轴，宏观总结正常。
- 问题：1040/1980 详情抓取失败（全部 `SerpAPIError`），1070/1974 单元 UNRESOLVED，
  报告"最活跃方向"出现 UNRESOLVED（1070 单元），质量被数据源配额明显拉低。
- 结论：SerpAPI `google_patents_details` 请求配额是当前瓶颈（而非代码/并发），
  8 并发、1980 件详情、重试 3 次仍大量 429/错误；与模型 16 并发无 429 形成对照。
- 后续建议：详情抓取增加 429 退避与配额感知调度；按配额把冻结上限默认调低；
  启用详情缓存（cache_entries）避免重跑重复抓取；必要时增加第二个详情 Provider 分流。
- 容器已重建，前端美化与抓取并发 16 已部署，后续可用小范围 Run 做 8/16 并发对照探针。

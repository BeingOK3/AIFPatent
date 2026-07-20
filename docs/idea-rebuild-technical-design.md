# AI4Patent IDEA 功能完全重构技术设计

> 文档状态：已实现并经真实 E2E 验证
> 版本：1.2
> 日期：2026-07-16
> 适用范围：AI4Patent 的“专利 IDEA 评审”模块
> 后续实现者：必须先完整阅读本文档和 `docs/development-log.md`

## 1. 文档目的

本文档是 IDEA 功能完全重构的唯一设计基线，用于约束后续 Agent 和开发者的实现方向。后续实现不得仅依赖旧的 `patent-IDEA-analyzer/SKILL.md` 自行推断架构。

本次重构保留旧 Skill 的总体业务流程：

```text
IDEA 解析
→ 检索规划
→ 现有技术检索
→ 候选筛选与全文核验
→ 新颖性判断
→ 创造性三步法
→ 发明价值预评估
→ 模拟审查意见
→ 完整报告
```

重构的核心不是改变业务流程，而是重新划分责任：

```text
Skill 负责触发和解释
Workflow 负责强制步骤和状态
Agent 负责语义判断
本地程序负责确定性处理
数据库负责持久化和证明
Validator 负责决定能否完成
```

## 2. 已确认且不得擅自更改的产品决策

### 2.1 新颖性结论

系统允许直接输出：

```text
结论：具备新颖性。
```

不得强制改写成“无法确认是否具备新颖性”。但所有明确结论必须同时展示：

- 结论依据；
- 已核验文献范围；
- 最接近对比文件；
- 未被单篇文献覆盖的必要技术特征；
- 检索数据源；
- 检索停止原因；
- 局限性；
- 结论置信度。

### 2.2 双路检索

每个检索计划默认并行尝试：

1. EXA MCP；
2. 本地实现的 Google Patents 检索工具。

两路结果必须标准化、合并并去重。任何一路失败时，另一路仍可继续。历史本地缓存作为第三层降级来源。

### 2.3 检索数量

- 检索规模必须根据技术范围宽窄和检索饱和度动态决定；
- 用户可以设置候选文献上限和深度核验上限；
- 深度核验默认目标和肯定性“具备新颖性”门槛为 10 个去重后的独立专利家族或独立技术披露；
- 不得用弱相关文献凑足 10 篇；
- 没有任何可用全文时 Run 必须失败；有 1–9 篇可用全文时继续完成证据分析并记录限制，不得给出肯定性“具备新颖性”结论，只能输出 `UNCERTAIN`，或在单篇文献已严格覆盖全部特征时输出 `NOT_NOVEL`；
- 深读不足的 Run 必须进入 `COMPLETED_WITH_LIMITATIONS`，报告同时展示实际篇数、配置下限和结论降级原因；
- 达到用户上限但检索尚未饱和时，允许输出结论，但必须标注“检索因用户上限终止”。

### 2.4 历史记录

- 每次用户提交都创建一个新的不可覆盖 Run；
- 每个 Run 的完整输入、配置、过程、证据和输出必须保存在本地；
- 历史结果不得被系统自动删除；
- 用户可以手动删除 Run 或整个 Case；
- 重新运行不得覆盖旧 Run；
- 所有内部使用者可以看到全部案例和历史结果，不设计用户级权限隔离。

### 2.5 缓存

- 缓存最大容量默认 1 GiB，即 `1073741824` 字节；
- 缓存超限时严格按 FIFO 清理；
- FIFO 只作用于可重新获取或重新计算的缓存；
- Run 输入、结构化结果、报告、审计记录和用户生成文件不属于缓存，绝不能被 FIFO 自动删除；
- 所有缓存参数由统一配置文件定义。

### 2.6 前端范围

当前只实现和展示 IDEA 功能。其他尚未重构的专利功能暂时隐藏，不在新前端中展示半成品入口。

## 3. 非目标

本阶段不包含：

- PCT 评审重构；
- 专利价值评估模块重构；
- CFP 侵权挖掘重构；
- Storage Deepdive 重构；
- 多租户、用户账号或细粒度权限；
- 远程对象存储；
- 分布式任务队列；
- 初期引入向量数据库；
- 将加权分数描述成正式法律意见。

## 4. 总体架构

```text
┌────────────────────────────────────────────────────────────┐
│ IDEA Web UI                                                │
│ 新建分析 / 进度 / 结果 / 历史 / 手动删除 / 重新运行         │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTP + SSE
┌──────────────────────────▼─────────────────────────────────┐
│ IDEA API + Workflow Orchestrator                           │
│ 状态机 / 重试 / 并发 / 恢复 / 完成门禁 / 配置加载           │
└───────────────┬──────────────────────────────┬─────────────┘
                │                              │
┌───────────────▼──────────────┐  ┌────────────▼─────────────┐
│ Deterministic Services       │  │ Restricted LLM Agents    │
│ 编号/日期/去重/缓存/矩阵/校验 │  │ 解析/映射/三步法/价值/审计 │
└───────────────┬──────────────┘  └────────────┬─────────────┘
                │                              │
┌───────────────▼──────────────────────────────▼─────────────┐
│ SQLite + Durable Run Store + FIFO Cache + Evidence Store   │
└────────────────────────────────────────────────────────────┘
```

### 4.1 Harness 工程原则

后续开发必须把 OpenCode/模型当作受控执行部件，而不是系统控制器：

1. 后端状态机决定下一步允许做什么；
2. Agent 只接收完成当前任务所需的最小上下文；
3. Agent 使用严格 JSON Schema 返回结果；
4. 程序验证 Schema、证据引用和状态转换；
5. 没有真实工具结果，不得记录工具成功；
6. 没有 `evidence_id`，不得把专利事实标记为已核验；
7. 子进程非零退出、超时或输出不完整不得发送 `COMPLETED`；
8. 每个步骤可独立重试，不得因单篇文献失败从头运行整个任务；
9. 对跨步骤仍需使用的完整结构化结果写入带 SHA-256 的 write-once `stage_results`；业务结果与检查点能同事务写入时必须同事务写入，恢复时优先复验检查点而不是重新调用模型；
10. 主 Agent 不加载全部专利全文；
11. 日志、数据库和 Git 共同形成可审计开发与运行记录。

## 5. 统一系统配置

### 5.1 配置文件

新增应用级唯一配置源：

```text
config/ai4patent.json
```

同时提供：

```text
config/ai4patent.schema.json
```

`ai4patent.json` 管理系统级存储、缓存、检索与 Workflow 设置。网页用户每次进入页面都必须重新输入模型 Base URL、API Key 和 Model；三者以 Run 级 `ContextVar` 隔离，API Key 只随 Run 创建/重跑请求进入后端进程内存，不得写入应用配置、认证文件、数据库、历史、日志、报告或浏览器存储。Base URL 与 Model 可作为不含凭证的 Run 来源信息持久化。CLI 必须显式提供 Base URL 和 Model，API Key 仅允许从当前进程指定的环境变量读取，不接受命令行明文参数。

OpenCode 自身配置由 `config/opencode/opencode.json` 承载，但后端使用的默认模型、功能开关、工作流、存储、缓存和检索策略均以 `config/ai4patent.json` 为准。

### 5.2 建议初始配置

```json
{
  "$schema": "./ai4patent.schema.json",
  "features": {
    "idea": true,
    "deepdive": false,
    "pct": false,
    "value": false,
    "cfp": false
  },
  "model": {
    "default": "agent-plan/glm-5.2",
    "structured_output_retries": 2
  },
  "storage": {
    "database": "data/ai4patent/ai4patent.db",
    "runs_dir": "workspace/idea-runs",
    "cache_dir": "workspace/cache",
    "document_store_dir": "workspace/cache/documents",
    "cache": {
      "max_bytes": 1073741824,
      "low_watermark_bytes": 966367642,
      "eviction_policy": "fifo",
      "cleanup_after_write": true
    },
    "history": {
      "auto_delete": false,
      "manual_delete_enabled": true
    }
  },
  "workflow": {
    "step_timeout_seconds": 600,
    "max_step_attempts": 3,
    "document_agent_concurrency": 3,
    "inventive_route_concurrency": 3,
    "resume_incomplete_runs_on_startup": true
  },
  "search": {
    "providers": {
      "exa_mcp": {
        "enabled": true,
        "timeout_seconds": 45,
        "max_attempts": 3
      },
      "google_patents_local": {
        "enabled": true,
        "timeout_seconds": 45,
        "max_attempts": 3,
        "base_url": "https://patents.google.com"
      },
      "local_cache": {
        "enabled": true
      }
    },
    "default_mode": "standard",
    "modes": {
      "quick": {
        "candidate_max": 30,
        "deep_review_min": 10,
        "deep_review_max": 10
      },
      "standard": {
        "candidate_max": 80,
        "deep_review_min": 10,
        "deep_review_max": 20
      },
      "deep": {
        "candidate_max": 150,
        "deep_review_min": 20,
        "deep_review_max": 40
      }
    },
    "saturation": {
      "consecutive_rounds": 2,
      "max_new_high_relevance_families": 1
    }
  },
  "outputs": {
    "always_save_markdown": true,
    "always_save_json": true,
    "generate_docx_by_default": false,
    "generate_xlsx_by_default": false
  },
  "logging": {
    "level": "INFO",
    "log_full_user_input": false,
    "log_full_model_output": false
  }
}
```

### 5.3 配置加载规则

- 服务启动时必须读取并校验配置；
- 配置无效时服务健康检查必须返回失败，不得静默使用散落在代码中的默认值；
- 允许环境变量覆盖非敏感路径；密钥只作为单次 Run 的临时运行凭证且不得出现在有效配置快照中；
- 每个 Run 保存启动时的配置快照；
- 修改配置只影响新 Run，不得改变历史 Run 的解释；
- 所有默认值只定义在配置或配置加载模块，业务代码中不得重复硬编码。

## 6. 前端 MVP

### 6.1 页面范围

只展示 IDEA 模块。推荐布局：

```text
┌──────────────┬──────────────────────────────┬─────────────────┐
│ 历史案例/Run │ 新建分析 / 当前结果           │ 进度与文件       │
│              │                              │                 │
│ 今天         │ IDEA输入                      │ 当前Workflow步骤 │
│ - KV缓存优化 │ 基础设置                      │ Provider状态     │
│ - NAND GC    │ 高级检索设置                  │ 检索统计         │
│              │ 结果标签页                    │ 输出文件         │
└──────────────┴──────────────────────────────┴─────────────────┘
```

窄屏下历史区改为抽屉。

### 6.2 用户输入

必填：

- IDEA 技术内容、方案描述或权利要求草稿。

可选：

- 案例标题，默认由模型生成；
- DOCX、PDF、PPTX、TXT 附件；
- 评估日期，默认当前日期；
- 分析范围：完整分析、仅新颖性、仅创造性；
- 重点申请人；
- 输出语言；
- 是否生成 DOCX/XLSX。

### 6.3 高级检索设置

普通用户只选择：

- 快速；
- 标准，默认；
- 深度；
- 自定义。

自定义字段：

- `candidate_max`；
- `deep_review_min`，不得低于 10；
- `deep_review_max`；
- 检索材料范围：专利、论文、标准、白皮书；
- 国家/地区；
- 语言。

校验要求：

```text
candidate_max >= deep_review_max
deep_review_max >= deep_review_min
deep_review_min >= 10
```

Provider 不是普通用户设置项。前端只显示 EXA、本地 Google Patents 和缓存的运行状态。

### 6.4 执行进度

前端展示 11 个真实步骤：

1. 输入准备；
2. IDEA 解析；
3. IDEA 模型校验；
4. 检索计划；
5. 双路检索；
6. 标准化、去重和全文抓取；
7. 文献深度核验；
8. 新颖性判断；
9. 创造性分析；
10. 价值预评估；
11. 证据审计和报告。

实时数字必须来自数据库：

- 两个 Provider 各自命中数；
- 合并去重数；
- 独立家族数；
- 摘要初筛进度；
- 全文抓取数；
- 深度核验进度；
- 高相关文献数；
- 当前重试次数；
- 降级原因。

### 6.5 结果标签页

1. 结论总览；
2. IDEA 技术特征；
3. 检索概览；
4. 候选文献；
5. 新颖性矩阵；
6. 创造性路线；
7. 发明价值；
8. 模拟审查意见；
9. 证据与审计；
10. 完整报告和文件。

### 6.6 历史记录

历史区域显示：

- 唯一的 Case 标题和短 Case ID；
- Run 状态、输入摘要和输入哈希；
- 创建时间；
- 状态；
- 新颖性结论；
- 创造性结论；
- 候选数和深度核验数；
- 运行耗时；
- 是否存在局限。

支持：

- 查看完整结果；
- 恢复某次 Run 的完整 IDEA、评估日、日期依据和预算快照；
- 相同配置重新运行；
- 修改 IDEA 后创建新 Run；
- 复制为新 Case；
- 导出；
- 手动删除 Run；
- 手动删除 Case 及其全部 Run；
- 后续增加 Run 对比。

所有内部使用者共享相同历史列表，不做个人空间隔离。

## 7. Case、Run 与持久化模型

### 7.1 Case

Case 表示一个可持续迭代的技术方案组。标题用于历史导航，不进入模型判断；新建标题按去除首尾空格后不区分英文大小写保持唯一，既有同名历史以短 Case ID 区分：

```text
idea_cases
  case_id TEXT PRIMARY KEY
  title TEXT NOT NULL
  created_at INTEGER NOT NULL
  updated_at INTEGER NOT NULL
  archived_at INTEGER NULL
```

### 7.2 Run

Run 表示一次不可覆盖的完整分析：

```text
idea_runs
  run_id TEXT PRIMARY KEY
  case_id TEXT NOT NULL
  parent_run_id TEXT NULL
  status TEXT NOT NULL
  evaluation_date TEXT NOT NULL
  date_basis TEXT NOT NULL
  analysis_scope TEXT NOT NULL
  model TEXT NOT NULL
  skill_version TEXT NOT NULL
  workflow_version TEXT NOT NULL
  config_snapshot TEXT NOT NULL
  limitation_json TEXT NOT NULL
  created_at INTEGER NOT NULL
  started_at INTEGER NULL
  completed_at INTEGER NULL
```

同一个 Case 可以包含多个 Run。每个 Run 保存独立输入和输入哈希，任何重新分析都必须创建新 `run_id`。前端查看历史 Run 时恢复其输入快照；用户编辑后提交表示创建同 Case 的方案变体，“重新运行”表示复制原输入和预算，两者均不得覆盖原 Run。

### 7.3 其他核心表

- `run_inputs`：完整输入文字、附件、文件哈希；
- `run_steps`：步骤、attempt、状态、输入输出哈希、错误；
- `tool_calls`：真实工具调用、参数、结果数、耗时、错误；
- `search_queries`：结构化查询计划；
- `search_hits`：Provider 原始命中和标准化结果；
- `patent_documents`：专利元数据和内容哈希；
- `patent_families`：同族关系；
- `run_documents`：文献在某个 Run 中的用途和状态；
- `idea_features`：F1–Fn 及其来源；
- `evidence`：原文证据、章节、offset、hash；
- `feature_mappings`：特征覆盖状态和证据；
- `novelty_results`：单篇覆盖聚合和结论；
- `inventive_routes`：D1/D2 路线结果；
- `value_results`：价值预评估；
- `audit_results`：审计结果；
- `reports`：Markdown/JSON 报告位置和哈希；
- `artifacts`：DOCX、XLSX 等文件；
- `cache_entries`：FIFO 缓存索引；
- `deletion_events`：用户手动删除的最小审计记录。

数据库使用独立的：

```text
data/ai4patent/ai4patent.db
```

不得使用 OpenCode 内部数据库作为业务数据库。

## 8. 本地文件布局

```text
workspace/
├── idea-runs/
│   └── {case_id}/
│       └── {run_id}/
│           ├── input/
│           ├── report.md
│           ├── report.json
│           ├── manifest.json
│           └── artifacts/
│               ├── report.docx
│               └── candidates.xlsx
└── cache/
    ├── documents/
    │   └── sha256/{content_hash}
    ├── searches/
    └── parsed/
```

持久数据：

- `idea-runs/**`；
- Case/Run 数据库记录；
- 报告；
- 用户附件；
- 审计结果；
- 报告引用的必要 evidence span。

可清理缓存：

- 原始网页响应；
- 可重新抓取的完整专利正文；
- 搜索响应缓存；
- 可重新生成的解析中间文件。

## 9. 1 GiB FIFO 缓存设计

### 9.1 FIFO 定义

FIFO 顺序以缓存项首次成功写入的 `created_at` 和单调递增 `sequence` 为准，不因访问而更新。不得实现成 LRU。

```text
cache_entries
  cache_key TEXT PRIMARY KEY
  category TEXT NOT NULL
  path TEXT NOT NULL
  size_bytes INTEGER NOT NULL
  content_hash TEXT NOT NULL
  sequence INTEGER NOT NULL
  created_at INTEGER NOT NULL
```

### 9.2 清理流程

写入缓存后：

1. 使用文件锁或数据库事务获取唯一清理权；
2. 计算所有有效缓存项总大小；
3. 若未超过 `max_bytes`，结束；
4. 按 `sequence ASC` 选择最早缓存；
5. 删除文件；
6. 删除或标记对应 `cache_entries`；
7. 持续清理直到不高于 `low_watermark_bytes`；
8. 记录清理数量、字节数和错误。

默认低水位为 0.9 GiB，用于避免每次写入后反复删除一个小文件。策略仍然是严格 FIFO。

### 9.3 边界条件

- 单个对象大于 1 GiB：可以临时使用，但不得写入缓存；
- 删除失败：记录错误并尝试下一项，不得删除持久结果；
- 数据库存在而文件不存在：启动修复任务清理悬空记录；
- 文件存在而数据库不存在：启动修复任务移动到隔离区或删除；
- 正在被步骤读取的缓存项：使用租约保护，租约结束后再参与 FIFO；
- 多 Run 引用同一缓存文档不阻止缓存淘汰，因为 Run 的完整报告和必要证据已持久化；
- 缓存淘汰后再次查看原始全文时，可以重新抓取。

## 10. 自适应检索与筛选漏斗

### 10.1 初始范围评估

Idea Parser 输出：

```text
narrow / medium / broad
```

参考：

- 技术领域数量；
- 必要特征具体程度；
- 上位词比例；
- 应用场景限制；
- 多技术交叉程度；
- 术语歧义。

初始判断只决定第一轮预算，不直接决定最终数量。

### 10.2 动态校正

每轮检索后统计：

- 新增独立家族；
- 新增强相关家族；
- 未覆盖必要特征；
- IPC/CPC 分散度；
- 同族重复率；
- 查询新增收益。

满足以下任一条件停止：

1. 达到用户设置上限；
2. 达到预算或超时；
3. 连续指定轮次新增强相关家族不超过阈值，且关键特征候选覆盖不再改善；
4. 所有 Provider 不可用且缓存不足。

### 10.3 四层漏斗

```text
L0 元数据：日期、编号、家族、重复、基本领域
L1 标题/摘要/IPC：强、中、弱、不确定、无关
L2 摘要+独权+发明概述：D1/D2/潜在新颖性威胁筛选
L3 全文证据核验：章节、实施例、要件映射、证据ID
```

不得仅因摘要未写某个特征就判定全文不存在该特征。`不确定` 文献必须进入 L2。

### 10.4 “完整读取”的系统定义

深度核验一篇文献表示：

- 全文已成功抓取并结构化，或存在等价可靠全文；
- 所有独立权利要求已检查；
- 摘要、背景、发明概述已检查；
- 与 F1–Fn 相关的说明书段落已核验；
- 每个肯定或部分覆盖判断都有 `evidence_id`；
- 不要求把与本案无关的全部实施例一次性输入模型。

## 11. 检索 Provider

### 11.1 统一接口

```text
SearchProvider.health_check()
SearchProvider.search(query)
DocumentProvider.fetch(document_ref)
```

Provider 返回统一结构：

```text
provider
query_id
publication_number
title
snippet
url
publication_date
raw_result_hash
```

### 11.2 EXA MCP Provider

- 由后端 MCP HTTP 客户端直接调用，不加载 OpenCode 会话上下文；
- 记录每次真实调用；
- 不把计划调用计为成功；
- 支持超时、重试和熔断；
- 使用当前 `web_fetch_exa` 契约，URL 以数组传入；全文字符预算由统一配置控制，当前为 300000；
- 兼容 Google Patents Markdown 标题紧凑格式，并分别提取 Info、Abstract、Description 和 Claims；
- 返回内容进入统一标准化流程。

### 11.3 本地 Google Patents Provider

本地工具直接构造和请求 Google Patents 搜索页及专利详情页，不依赖 EXA MCP。

职责：

- 构造 Google Patents 查询；
- 分页；
- 解析搜索结果；
- 直接按专利号抓取详情页；
- 提取元数据、摘要、权利要求和说明书；
- 识别页面结构变化；
- 限速、重试和缓存；
- 返回统一 Provider 数据结构。

本地工具消除 MCP 单点，但仍可能受本地网络、Google Patents 限流或页面变化影响。因此必须与缓存和 EXA 并行，而不是声称绝对不会失败。

“本地”表示 Provider 代码由本项目维护，并不表示已经建立离线专利镜像。本次 Run 检索阶段全部失败的 Provider 在全文阶段降为备路，避免每篇文献重复等待同一网络超时；主路抓取失败时仍会尝试该备路。

### 11.4 合并与去重

优先级：

1. 标准化公开号相同；
2. 标准化申请号相同；
3. 已知同族关系；
4. 标题、优先权和申请人组合相似，仅作为待确认同族。

合并后保留来源：

```json
{
  "publication_number": "US12345678B2",
  "found_by": ["exa_mcp", "google_patents_local"],
  "query_ids": ["Q1", "Q4"]
}
```

全文深读队列只接受可标准化且能回指本次合并候选的公开号；仅有 URL、缺少公开号的结果仍保留在候选和限制记录中，但不得以空标识进入抓取。队列按标准化公开号再次去重。并发抓取中的任一内部异常必须先取消并等待同批其余任务结束，再由 Workflow 决定是否重试，避免失败 attempt 的网络请求泄漏到下一 attempt 或 Run 终态之后。

## 12. Workflow 状态机

### 12.1 步骤

```text
PREPARE_INPUT
PARSE_IDEA
VALIDATE_IDEA_MODEL
PLAN_QUERIES
RETRIEVE_CANDIDATES
NORMALIZE_AND_FETCH
ANALYZE_DOCUMENTS
DETERMINE_NOVELTY
ANALYZE_INVENTIVENESS
ASSESS_VALUE
AUDIT_AND_REPORT
```

### 12.2 Run 状态

- `QUEUED`；
- `RUNNING`；
- `COMPLETED`；
- `COMPLETED_WITH_LIMITATIONS`；
- `FAILED`；
- `CANCELLED`。

### 12.3 完成门禁

`COMPLETED` 至少要求：

- 输入和配置快照存在；
- 必需步骤成功；
- 结论所引用的文献存在；
- 所有关键证据可以重新定位；
- 新颖性矩阵通过单篇原则验证；
- 创造性路线引用有效 D1/D2；
- 审计不存在 critical issue；
- `report.md` 和 `report.json` 存在且哈希一致；
- 子进程退出码为 0；
- 不存在未消费的 fatal error。

`COMPLETED_WITH_LIMITATIONS` 允许：

- 仅一个在线 Provider 成功；
- 用户上限导致未饱和；
- 部分低优先级文献抓取失败；
- 相关且可用的深读全文少于配置下限，但至少有一篇，并且结论按门禁降级；
- 个别字段无法核实。

该状态可给出门禁允许的结论，但报告必须展示限制；深读不足时不得输出肯定性“具备新颖性”。

## 13. Agent 设计

只设计 7 类 Agent，运行时可以有多个实例：

### 13.1 `patent-idea-parser`

输出技术领域、技术问题、F1–Fn、技术效果、主题类型、范围宽窄。每个特征标注：

- `explicit`；
- `normalized`；
- `inferred`；
- 用户原文 source span。

对于 `explicit` 特征，模型负责逐字复制引用文本，后端负责确定字符位置：模型偏移已经能精确回指时直接接受；偏移不准但引用文本能在输入中找到时，按最接近建议位置的精确出现位置修正；引用出现多次时使用同一确定性规则；引用文本不存在或为空时严格失败。不得要求模型独自承担中文 Unicode offset 计算，也不得用模糊匹配伪造 source span。

模型服务返回 HTTP 成功但 assistant content 为空属于可重试的结构化输出失败，必须先在单次 Agent 调用的结构化重试预算内处理；只有预算耗尽后才上升为 Workflow step 失败，避免把短暂空响应直接放大为完整步骤重跑。

### 13.2 `patent-query-planner`

只输出中英文术语、同义词、上位词、功能等价描述、IPC/CPC 候选和分轮检索计划，不执行搜索。

### 13.3 `patent-document-analyzer`

一篇文献一次完成：

- 技术问题、方案、效果、场景；
- 独立权利要求分析；
- F1–Fn 覆盖映射；
- evidence ID；
- 覆盖状态和置信度。

模型只看到文档内短证据别名 `E1..En`；后端在持久化前将 `E1`、`E-1` 等无歧义写法映射回 Run/文档作用域内的真实哈希 evidence ID，越界或未知引用继续严格失败。公开号属于不可变输入元数据，模型回显错误时由后端恢复请求中的真实值，不能因为回显字段浪费整批分析。

逐篇并发分析任一任务失败时，必须取消并等待同批兄弟任务结束后才能进入 Workflow 重试，防止前一 attempt 在后台继续写入。`patent_documents` 是跨 Run 共享表，只有不存在任何引用该文档且尚未深读的 Run 时才能释放可重建全文，不能破坏并发 Run。

高风险文献调用第二个独立实例复核。

### 13.4 `patent-inventive-step-analyzer`

每个 D1 路线一个实例。若证据不足，返回 `NEED_MORE_EVIDENCE`，不得自行搜索或编造 D2。

### 13.5 `patent-value-analyzer`

负责可取证性、可规避性、技术/市场价值和申请建议，不得修改新颖性或创造性结论。

### 13.6 `patent-evidence-auditor`

只读检查证据、日期、单篇原则、章节和结论强度，只输出问题列表，不直接重写结论。

### 13.7 `patent-report-composer`

只读取已审计的结构化结果。专利号、日期、章节号和统计数字由程序注入。

## 14. Skill 重构

保留一个用户入口 Skill：

```text
patent-idea-review
```

入口 Skill 只负责：

- 触发条件；
- 输入识别；
- 调用 workflow 工具；
- 状态和失败解释；
- 报告呈现；
- 禁止绕过 Workflow 自行模拟检索。

建议目录：

```text
patent-idea-review/
├── SKILL.md
├── references/
│   ├── idea-normalization.md
│   ├── search-planning.md
│   ├── evidence-policy.md
│   ├── novelty-rules-cn.md
│   ├── inventive-step-rules-cn.md
│   ├── value-assessment.md
│   └── report-policy.md
├── schemas/
└── scripts/
```

主 `SKILL.md` 目标为 100–180 行，详细规则按 Agent 需要加载。旧 Skill 在新工作流完成迁移并通过回归测试前不得直接删除，应通过功能开关逐步切换。

## 15. 报告输出

每个 Run 始终保存：

```text
report.md
report.json
manifest.json
```

`report.json` 是权威结构化结果，`report.md` 是用户可读版本。

`report.md` 必须把申请建议、创造性状态和审计等级翻译为中文，价值维度显示为 `n/5`，所有可标准化公开号生成可点击的 Google Patents 链接；`report.json` 保留稳定枚举码供程序消费。

完整报告至少包含：

1. 结论总览；
2. IDEA 技术特征；
3. 评估日期和日期依据；
4. 检索计划和实际执行；
5. Provider 状态与结果合并统计；
6. 候选文献列表；
7. 深度核验文献；
8. 新颖性矩阵和结论；
9. 创造性多 D1 路线；
10. 价值预评估；
11. 模拟审查意见；
12. 证据审计；
13. 局限性；
14. 模型、Skill、Workflow 和配置版本。

## 16. API 设计

```text
POST   /api/idea/cases
GET    /api/idea/cases
GET    /api/idea/cases/{case_id}

POST   /api/idea/cases/{case_id}/runs
GET    /api/idea/runs/{run_id}
GET    /api/idea/runs/{run_id}/events
GET    /api/idea/runs/{run_id}/debug
POST   /api/idea/runs/{run_id}/cancel
POST   /api/idea/runs/{run_id}/rerun
GET    /api/idea/runs/{run_id}/report
GET    /api/idea/runs/{run_id}/artifacts

DELETE /api/idea/runs/{run_id}
DELETE /api/idea/cases/{case_id}

GET    /api/system/config
GET    /api/system/health
GET    /api/system/cache
POST   /api/system/cache/cleanup
```

### 16.1 删除语义

- 删除 Run：删除该 Run 的输入快照、持久结果和生成文件；
- 删除 Case：二次确认后删除其所有 Run；
- 删除操作不得直接删除仍被其他 Run 引用的共享缓存文件；
- 共享缓存由 FIFO 统一管理；
- `deletion_events` 只保存最小操作记录，不保留被删除的完整内容；
- 当前无用户系统，删除者字段可以为空或保存前端提供的内部操作者标签。

## 17. 健康检查与可观测性

`/api/system/health` 必须分别报告：

- FastAPI；
- 数据库读写；
- 固定 Workflow 恢复器（OpenCode 可执行文件不是 IDEA 运行依赖）；
- 模型配置；
- EXA MCP；
- 本地 Google Patents；
- 缓存目录；
- 当前缓存大小；
- Workflow 恢复器。

不得再用“HTTP 服务能响应”代表整个系统健康。

日志必须包含：

- `run_id`；
- `step_name`；
- `attempt`；
- `provider`；
- `duration`；
- 状态和错误码。

每个 Run 的详细调试事件追加到 `workspace/debug/idea-runs/{run_id}.jsonl`。前端通过 `/api/idea/runs/{run_id}/debug` 聚合最新步骤 attempt、Tool Call 摘要和 JSONL 事件；该目录整体由 Git 忽略。调试数据只保存输入字符数、Provider、操作、状态、结果数、耗时、usage 计数和错误摘要，不保存 API Key、Authorization、完整 Prompt、完整用户输入或完整模型输出。

面向用户的创造性、价值、审计、限制和报告说明不仅在 Prompt 中要求简体中文，还必须通过结构化客户端的语言门禁。无中文或明显以英文为主的说明视为验证失败，并在既定结构化重试预算内要求模型修正；重试耗尽则该步骤失败，不得持久化英文判断。历史报告与 Manifest 不可改写，前端对修复前英文报告显示中文兼容说明并提示重新运行。

运行日志不默认打印完整 IDEA、完整权利要求、完整工具输出或完整模型输出。内部共享不等于应把大文本和密钥写进日志。

## 18. 测试策略

### 18.1 单元测试

- 配置加载和 Schema 校验；
- 专利号标准化；
- 日期过滤；
- 同族和公开号去重；
- FIFO 顺序；
- 1 GiB 上限和低水位；
- 缓存项租约；
- 状态机合法/非法转换；
- 新颖性单篇矩阵；
- Run 不可覆盖；
- 用户手动删除；
- 报告清单完整性。

### 18.2 Provider 契约测试

对 EXA 和本地 Google Patents 使用相同契约：

- 正常结果；
- 空结果；
- 超时；
- 限流；
- 非法页面；
- 分页；
- 重复结果；
- 页面字段缺失；
- Provider 单路故障；
- 两路同时故障并使用缓存。

外部网络测试与离线 fixture 测试分离，CI 默认使用 fixture，手动执行在线 smoke test。

### 18.3 Workflow 集成测试

- 标准成功 Run；
- 深度核验不足 10 篇；
- 用户上限触发；
- 检索饱和停止；
- 同一服务进程内中途断开页面后继续执行；服务重启因临时 Token 丢失而明确失败，重新输入后才能重跑；
- 单篇分析失败重试；
- audit critical issue 阻止 `COMPLETED`；
- `COMPLETED_WITH_LIMITATIONS` 仍生成明确结论；
- 页面刷新后恢复进度；
- 服务重启后保留未完成任务历史，并以 `RUNTIME_API_KEY_REQUIRED_AFTER_RESTART` 终止，不持久化 Token 自动恢复。

### 18.4 前端测试

- 仅显示 IDEA；
- 基础表单校验；
- 自定义数量上下限校验；
- SSE 断线重连；
- 历史列表；
- Run 详情；
- 重新运行不覆盖旧记录；
- 手动删除确认；
- Provider 降级展示；
- 结论和局限性同时展示。

### 18.5 Agent/Eval 测试

建立固定评测案例：

- 已知单篇破坏新颖性；
- 已知具备新颖性；
- 已知需要 D1+D2；
- 后公开文献；
- 大量同族；
- 摘要不相关但 Claim 相关；
- 章节引用错误；
- Provider 故障；
- 检索范围极宽和极窄。

测量：

- 检索召回率；
- 高相关准确率；
- 章节引用正确率；
- 要件映射一致率；
- 虚假证据率；
- 新颖性结论稳定性；
- Token、时延和费用；
- 故障恢复率。

## 19. 后续开发协议：Harness、追加日志和 Git

### 19.1 必读文件

每个后续 Agent 开始工作前必须读取：

1. `AGENTS.md` 或适用的上级规则；
2. 本文档；
3. `docs/development-log.md` 全文；
4. 当前功能涉及的 Skill/Agent/Schema；
5. `git status` 和最近 Git 历史。

### 19.2 工作单元

开发必须拆成可独立测试和提交的 Work Unit，例如：

```text
IDEA-CONFIG-001
IDEA-DB-001
IDEA-CACHE-001
IDEA-GPAT-001
IDEA-WF-001
IDEA-UI-001
```

每个 Work Unit 只能有一个明确目标，不得把多个大功能混在一次提交。

### 19.3 只追加开发日志

`docs/development-log.md` 是 append-only 文件：

- 只能在文件末尾增加新条目；
- 禁止删除、重排或修改旧条目；
- 旧条目有错误时，新增“更正”条目引用原 Work Unit；
- 每个功能完成前必须记录日期、目标、实现、文件、测试、结果、限制和提交主题；
- Work Unit ID 必须出现在日志和 Git commit subject 中；
- Git hash 通过 `git log --grep <Work Unit ID>` 查找，不要求回写旧日志。

### 19.4 每个功能的强制顺序

```text
1. 读取设计和日志
2. 检查工作区，保护用户已有改动
3. 声明当前 Work Unit
4. 实现最小完整功能
5. 运行与风险相称的测试
6. 修复直到测试通过
7. 复核 git diff
8. 在 development-log.md 末尾追加条目
9. 再运行关键测试和 git diff --check
10. 只暂存本 Work Unit 文件
11. Git 提交
12. 报告提交主题、测试和剩余问题
```

禁止：

- 功能未测试就提交；
- 把用户未跟踪文件加入提交；
- 使用 `git add .`；
- 修改或覆盖旧日志；
- 使用 `git reset --hard`；
- 强制推送；
- 为制造整洁历史而改写已有共享提交；
- 仅凭 Agent 声称成功，不检查测试和 diff。

### 19.5 Commit 规范

```text
feat(idea): [IDEA-CACHE-001] implement FIFO cache limit
fix(idea): [IDEA-WF-003] preserve error state after timeout
test(idea): [IDEA-GPAT-002] add parser fixtures
docs(idea): [IDEA-DESIGN-001] define rebuild architecture
```

一次提交对应一个已测试 Work Unit。测试失败时不得提交“完成”状态，可以提交明确标注的测试基础设施或诊断 Work Unit。

## 20. 实施里程碑与建议提交边界

### M0：设计基线

- 本文档；
- 追加开发日志；
- 配置、数据库和 API 决策冻结。

### M1：Harness 基础

1. `IDEA-CONFIG-001`：统一配置及 Schema；
2. `IDEA-DB-001`：数据库迁移和 Case/Run；
3. `IDEA-RUNSTORE-001`：持久 Run 目录与 manifest；
4. `IDEA-CACHE-001`：1 GiB FIFO 缓存；
5. `IDEA-HEALTH-001`：完整健康检查。

### M2：检索基础

1. `IDEA-PROVIDER-001`：Provider 契约；
2. `IDEA-GPAT-001`：本地 Google Patents 搜索；
3. `IDEA-GPAT-002`：全文抓取和解析；
4. `IDEA-EXA-001`：EXA 适配；
5. `IDEA-MERGE-001`：合并、去重和同族；
6. `IDEA-SEARCH-001`：自适应检索与饱和。

### M3：Workflow 与 Agent

1. `IDEA-WF-001`：状态机和恢复；
2. `IDEA-SCHEMA-001`：Agent JSON Schema；
3. `IDEA-PARSE-001`：Idea Parser；
4. `IDEA-DOC-001`：Document Analyzer；
5. `IDEA-NOVELTY-001`：新颖性矩阵；
6. `IDEA-INVENT-001`：多 D1 路线；
7. `IDEA-VALUE-001`：价值分析；
8. `IDEA-AUDIT-001`：证据审计；
9. `IDEA-REPORT-001`：结构化报告。

### M4：前端 MVP

1. `IDEA-UI-001`：仅 IDEA 导航和新建表单；
2. `IDEA-UI-002`：Workflow 进度；
3. `IDEA-UI-003`：结果标签页；
4. `IDEA-UI-004`：历史 Case/Run；
5. `IDEA-UI-005`：重新运行、导出和手动删除。

### M5：回归、迁移和切换

1. 旧 Skill 案例回归；
2. 故障和恢复测试；
3. Token/耗时基线；
4. 功能开关切换新 IDEA；
5. 确认无依赖后归档旧 Skill；
6. 更新用户 README。

## 21. MVP 验收标准

满足以下条件才可以宣布 IDEA 重构完成：

- 前端只显示 IDEA；
- 用户可以提交文本和附件；
- 用户可以选择检索深度和自定义上限；
- EXA 和本地 Google Patents 默认并行；
- 双路结果合并去重；
- 候选先经摘要/独权筛选；
- 深度核验默认最低目标为 10；
- 具备动态检索和饱和停止；
- 可直接输出“具备新颖性”等明确结论；
- 报告同时显示依据、置信度和局限性；
- Run 全量本地保存且服务重启后可查看；
- 重新运行不覆盖旧记录；
- 历史结果不自动删除；
- 用户可以手动删除；
- 缓存超过 1 GiB 后按 FIFO 自动回落；
- FIFO 不删除任何 Run 结果；
- 超时、错误和非零退出不会被显示成完成；
- 页面刷新后可恢复进度；
- 关键步骤具备自动测试；
- 所有 Work Unit 有追加日志和 Git 提交记录。

## 22. 后续实现者的决策边界

可以自行决定：

- Python 模块内部命名；
- 前端组件拆分；
- SQLite ORM 或轻量 SQL 层；
- fixture 组织方式；
- 报告具体样式；
- 低风险内部算法实现。

不得自行决定：

- 取消明确的新颖性结论；
- 取消双路检索；
- 把最低目标降到 10 以下；
- 把全部全文塞入主 Agent；
- 让模型自行确认工具调用成功；
- 自动删除历史 Run；
- 让 FIFO 删除持久结果；
- 恢复显示其他未完成模块；
- 绕过追加日志、测试或逐功能 Git 提交；
- 将完整旧 Skill 原样复制给所有子 Agent。

如实现过程中发现必须改变上述固定决策，应新增设计变更文档和开发日志条目，说明原因、影响和迁移方案，不得静默偏离。

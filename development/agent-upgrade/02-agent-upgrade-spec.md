# Agent 升级规格

版本：`v1`
目标：把当前固定专利流水线演进为“证据约束、可恢复、有预算、有人工审批门”的研究 Agent。

## 1. 产品场景

### 核心用户任务

用户提交一项技术创意后，系统应：

1. 判断输入是否足以开始；不足时只问高信息增益问题。
2. 形成可审计研究目标、约束、特征和预算。
3. 在冻结语料内检索和补证，根据证据覆盖自主决定下一步。
4. 必要时建议扩大检索，但需用户审批后建立 child research run。
5. 当证据充分、搜索饱和、预算耗尽或风险过高时明确停止。
6. 给出引用可验证的分析、限制和建议，而非无依据的确定性法律结论。

### 求职展示故事

演示不应强调“模型数量”，而应展示一条可重放轨迹：

`Goal -> Decision -> Tool Action -> Observation -> Context Snapshot -> Decision -> Stop -> Cited Answer`

面试可解释的技术点：

- bounded autonomy；
- deterministic guardrails；
- event-sourced audit；
- context engineering；
- retrieval/evidence evaluation；
- idempotent recovery；
- human-in-the-loop；
- cost/latency/quality trade-off。

## 2. 非目标

- 不做开放网络通用浏览器 Agent。
- 不让模型执行任意 Python、Shell、SQL 或 URL。
- 不在首版实现角色化多 Agent 协商。
- 不把模型输出当作状态迁移、权限或引用真实性的最终判定。
- 不承诺法律意见或“检索完整性”。

## 3. 总体结构

```text
API / SSE
   |
Agent Application Service
   |
Bounded Supervisor ---- Policy / Budget / Approval Gates
   |                              |
   +---- Context v2               +---- Human approval
   |
Tool Registry
   +---- retrieve_in_scope
   +---- inspect_document
   +---- verify_feature_coverage
   +---- propose_external_research
   +---- ask_clarification
   +---- finalize
   |
PostgreSQL Agent Store (single source of truth)
   +---- goals / decisions / actions / observations
   +---- context manifests / budget ledger / approvals
   |
Existing Corpus / Evidence / Citation / Provider adapters
```

外层仍保留确定性阶段，例如输入落库、语料导入、最终审计和产物生成。自主循环只用于“不确定且需要观察后选择下一步”的研究阶段。

## 4. Agent 状态模型

建议新增领域对象：

### `AgentGoal`

- `agent_run_id`
- `parent_run_id`
- `goal_type`: `INITIAL_REVIEW | FOLLOWUP | NEW_RESEARCH`
- `user_intent`
- `success_criteria`
- `frozen_constraints`
- `risk_level`
- `created_at`

### `AgentBudget`

- `max_decisions`
- `max_tool_actions`
- `max_external_queries`
- `max_documents`
- `max_input_tokens`
- `max_output_tokens`
- `deadline_at`
- `spent_*`

所有扣减由代码原子执行；模型只看剩余预算，不能自行修改。

### `AgentDecision`

- `decision_id`
- `sequence`
- `state_version`
- `action_type`
- `rationale_summary`
- `expected_information_gain`
- `arguments`
- `policy_version`
- `prompt_version`
- `model`
- `created_at`

不保存隐藏 chain-of-thought。`rationale_summary` 只保存简短、面向审计的理由。

### `AgentAction`

- `action_id`
- `decision_id`
- `tool_name`
- `validated_arguments`
- `idempotency_key`
- `status`
- `started_at/completed_at`
- `error_code`
- `cost`

### `AgentObservation`

- `observation_id`
- `action_id`
- `result_type`
- `result_ref`
- `summary`
- `content_hash`
- `scope_hash`
- `created_at`

大正文不复制进事件表，只引用不可变 corpus/version/chunk。

### `AgentContextSnapshot`

- `context_id/context_hash`
- `agent_run_id/state_version`
- `layers`
- `token_estimator_version`
- `selected_item_ids/excluded_item_ids`
- `used_tokens/reserved_tokens`
- `limitations`

### `AgentStop`

- `reason`: `ANSWER_READY | NEED_CLARIFICATION | NEED_APPROVAL | SATURATED | BUDGET_EXHAUSTED | POLICY_BLOCKED | INSUFFICIENT_EVIDENCE | FAILED`
- `evidence_coverage`
- `limitations`
- `final_context_id`

## 5. 有界决策循环

```text
load durable state
  -> deterministic pre-check
  -> build Context v2
  -> model proposes exactly one action
  -> schema validation
  -> policy/scope/budget validation
  -> persist decision
  -> reserve idempotent action
  -> execute tool
  -> persist observation and budget
  -> deterministic completion/saturation check
  -> loop or stop
```

模型允许的动作：

| 动作 | 用途 | 关键约束 |
|---|---|---|
| `ASK_CLARIFICATION` | 输入含糊或缺失关键事实 | 最多 3 个问题；进入等待用户状态 |
| `RETRIEVE_IN_SCOPE` | 在 frozen versions 内补证 | 必须带 feature/query/section；不可逃逸 scope |
| `INSPECT_DOCUMENT` | 查看指定 version 的 claims/description | 只能读取已授权 version |
| `VERIFY_COVERAGE` | 运行确定性覆盖/引用检查 | 无外部副作用 |
| `PROPOSE_NEW_RESEARCH` | 建议扩大检索 | 只生成 proposal，不执行外部检索 |
| `FINALIZE` | 证据和审计门通过后结束 | 代码再次验证 coverage/citation |

Provider search/fetch 不直接暴露给模型。模型只能提出研究意图，由 Research Tool 内部执行 provider 路由、并发、回退和熔断。

### 停止策略

以下规则由代码先于模型执行：

- 达到用户目标且引用/覆盖门通过：`ANSWER_READY`。
- 连续两轮没有新增 patent family、没有 coverage 提升：`SATURATED`。
- 决策、查询、token、文档或时间预算耗尽：`BUDGET_EXHAUSTED`。
- 输入关键事实不足：`NEED_CLARIFICATION`。
- 需要扩域、额外费用或法律判断：`NEED_APPROVAL`。
- 证据不足但不能再安全行动：`INSUFFICIENT_EVIDENCE`。

“模型觉得完成”不是完成条件，只是 `FINALIZE` 提案。

## 6. Context v2

### 分层

按不可混淆的类型组装：

1. `POLICY`：系统规则、工具契约、法律/安全边界。
2. `GOAL`：用户目标、冻结约束、预算、当前任务。
3. `FACTS`：解析后的特征、日期、scope、已批准事实。
4. `MEMORY`：持久化的决策/观察摘要，不作为专利证据。
5. `EVIDENCE`：本轮允许引用的专利 chunks，带 `C1..Cn`。
6. `OUTPUT_CONTRACT`：下一动作 schema。

### 预算

- 使用真实 tokenizer；fallback 采用保守 CJK/ASCII 估算。
- 为每层配置最小/最大配额，先保证 Policy、Goal 和必要 claims。
- evidence 按问题类型、section、feature coverage、diversity 和分数选择。
- 超预算内容进入 excluded list，记录原因。
- `context_hash` 覆盖顺序、内容 hash、scope、策略和 estimator 版本。

### 记忆

- 不保存无限对话全文。
- 最近 N 个 Turn 保留结构化摘要；用户原话按引用需求单独存储。
- 工作记忆来自持久事件投影，不能来自 Python 进程字典。
- 历史报告和历史对话永远是 Note，不得成为 Citation。

## 7. 数据与恢复

### 新表建议

- `agent_runs`
- `agent_goals`
- `agent_budgets`
- `agent_decisions`
- `agent_actions`
- `agent_observations`
- `agent_context_snapshots`
- `agent_approvals`
- `agent_stops`

必须具有：

- `state_version` 乐观锁；
- `(agent_run_id, sequence)` 唯一约束；
- `idempotency_key` 唯一约束；
- action lease 和过期恢复；
- JSON schema/version 字段；
- 外键删除策略和显式 retention policy。

### 恢复规则

1. 启动时扫描非终态 Agent run。
2. 没有 BYOK 时转 `WAITING_FOR_CREDENTIAL`，不标记业务失败。
3. 已完成 action 有 observation：从下一 decision 恢复。
4. action 已预留但无结果：按 idempotency key 查询/安全重试。
5. checkpoint 与数据库冲突：数据库事件序列为准，重建图状态。
6. 每次恢复都写 `RECOVERY` observation。

## 8. 人工审批门

必须审批：

- 从 frozen corpus 扩大到外部搜索；
- 创建 child research run；
- 提高查询/token/文档/费用预算；
- 删除或跨库清理数据；
- 高风险法律结论；
- 更换会影响数据外发边界的 Provider。

审批记录包括 proposal hash、范围、预计成本、有效期、批准人和结果。批准只授权该 hash 对应动作，不做长期通配授权。

## 9. 实施路线

### Phase 0：可测基线与减债

工作包：

- `P0-1` 定位 API 测试阻塞，建立可重复的全量测试命令。
- `P0-2` 修复中文 token 计量，给 Context manifest 增加 estimator version。
- `P0-3` 统一 VectorIndex 协议。
- `P0-4` 建立配置字段使用清单，弃用无使用点字段。
- `P0-5` 明确 SQLite/PostgreSQL 数据权威和跨库删除策略。
- `P0-6` 决定 Redis：接入任务 lease，或从当前部署必需项移除。

验收门：

- 基线测试无挂起；
- Context 中英文预算测试通过；
- 删除 dry-run 能列出所有跨存储对象；
- 新代码不引入第三套状态/向量协议。

### Phase 1：持久 Agent 内核（无自主循环）

新增 `backend/agent_core/`：

- `domain.py`
- `repository.py`
- `policy.py`
- `budget.py`
- `context.py`
- `tools.py`
- `service.py`

先让现有 Followup 七步通过 Agent event store 执行，行为保持不变。移除 `_TurnMemory`，支持进程重启恢复。

验收门：

- kill/restart 后继续同一 Turn；
- 已完成工具不重复执行；
- 没有 API key 时进入等待而非失败；
- 每个输出可从持久事件重放。

### Phase 2：有界自主检索

- 引入单动作 `AgentDecision` schema。
- 将追问的 plan/retrieve/answer 改为条件循环。
- 接入范围、预算、审批、饱和和引用门。
- 实现 `ASK_CLARIFICATION/RETRIEVE_IN_SCOPE/VERIFY_COVERAGE/FINALIZE`。

验收门：

- 模拟决策集全部满足硬性安全指标；
- 最大循环次数和每类预算不可突破；
- scope escape、无效 Citation、重复副作用均为 0。

### Phase 3：受控新研究与持久任务

- 实现 `PROPOSE_NEW_RESEARCH -> approval -> child run`。
- 采用 PostgreSQL workflow lease/idempotency 作为首版持久协调；只有吞吐或跨进程限流明确超过 PostgreSQL 方案边界时，才评估 Redis queue/lease。
- Provider 调用加全局限流、幂等、费用和健康度路由。
- child run 完成后以 observation 回到父 Agent。

验收门：

- 未批准时外部查询数为 0；
- worker 异常后任务可被重新 claim；
- 同一 proposal 不生成多个 child run；
- 删除/retention 覆盖父子运行和证据关系。

### Phase 4：首次分析与 Landscape 收敛

- 将首次分析中的 query planning/retrieval 补证迁入同一 Agent 内核。
- 抽取统一 EvidenceService/ContextPolicy。
- Landscape 只保留业务特有状态，将检索、语料、引用、任务能力复用。
- 用真实匿名样本替换/扩展模拟集。

验收门：

- 三个入口共享同一 Agent event contract；
- 不再存在重复的证据 alias/citation 规则；
- 对比固定流程基线，有可量化质量收益且成本在预算内。

## 10. 每个工作包的交付模板

Coding Agent 提交时必须说明：

```text
工作包 ID：
改动目标：
源码证据：
设计决策：
修改文件：
数据库迁移：
兼容策略：
测试命令与结果：
模拟评测变化：
安全/成本影响：
未完成项：
回滚方式：
```

禁止只写“优化 Agent/重构架构”这类不可验收描述。

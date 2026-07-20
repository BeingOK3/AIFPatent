# AI4Patent 追加式开发日志

> 本文件只允许在末尾追加。禁止删除、重排或修改既有条目。
> 发现旧记录有误时，新增更正条目并引用原 Work Unit ID。

## 2026-07-16 — IDEA-DESIGN-001

- 类型：技术设计基线
- 目标：固定 IDEA 完全重构的产品、Workflow、Agent、检索、缓存、持久化、前端、测试和 Git 开发协议。
- 实现：新增 `docs/idea-rebuild-technical-design.md`，确立一个入口 Skill、11 个 Workflow 步骤、7 类受限 Agent、EXA 与本地 Google Patents 双路检索、摘要/独权筛选漏斗、明确新颖性结论、Case/Run 永久历史、1 GiB FIFO 缓存和仅 IDEA 前端范围。
- 固定决策：内部全部共享可见；历史结果不自动删除；用户可手动删除；FIFO 只清理可重建缓存；应用配置集中到未来的 `config/ai4patent.json`。
- 涉及文件：`docs/idea-rebuild-technical-design.md`、`docs/development-log.md`。
- 验证：待本 Work Unit 完成文档结构、JSON 示例和 Git diff 校验后执行提交。
- 提交主题：`docs(idea): [IDEA-DESIGN-001] define rebuild architecture`
- 已知限制：本 Work Unit 只创建设计与开发协议，不实现运行时代码。

## 2026-07-16 — IDEA-DESIGN-001-VERIFY

- 类型：验证记录
- 关联工作单元：`IDEA-DESIGN-001`
- 首次校验：失败；校验脚本误将缓存视为顶层 `cache`，实际设计为 `storage.cache`，未发现文档配置缺陷。
- 修正处理：仅修正验证脚本对配置层级的预期，未修改已确认设计。
- 最终验证：通过 `git diff --check`；通过 Python `json.loads` 解析配置示例；通过 1 GiB、FIFO、历史不自动删除、允许手动删除、仅启用 IDEA 及 19 个主章节的断言。
- 验证结果：`DOC_CHECK_OK`；技术设计文档 1167 行。

## 2026-07-16 — IDEA-CONFIG-001

- 类型：Harness 统一配置
- 目标：建立 IDEA 应用唯一配置源和启动阶段的严格校验，防止业务代码散落默认值或静默降级。
- 实现：新增 `config/ai4patent.json` 与 JSON Schema；新增 Pydantic 严格配置模型、环境变量配置路径覆盖、相对路径归一化和可序列化 Run 快照。
- 固定约束：仅 IDEA 功能可启用；历史不自动删除；缓存只能 FIFO；默认上限 1 GiB；深度核验下限不得低于 10；密钥只保存环境变量名和本地认证文件引用。
- 涉及文件：`config/ai4patent.json`、`config/ai4patent.schema.json`、`backend/idea/config.py`、`backend/idea/__init__.py`、`backend/tests/test_config.py`、`backend/tests/__init__.py`。
- 首轮测试：1 项失败；测试误将字段名 `api_key_env` 判定为真实密钥，实现中不存在密钥值。
- 修正：改为结构化断言 `model` 中不存在 `api_key` 或 `apiKey` 字段。
- 最终测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_config -v`，6 项全部通过；两个 JSON 文件通过 `json.tool`；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-CONFIG-001] add validated system configuration`
- 已知限制：本 Work Unit 只建立配置基础，后续 API 和 Workflow 接入将分别在对应 Work Unit 完成。

## 2026-07-16 — IDEA-DB-001

- 类型：Harness SQLite 持久化
- 目标：建立独立于 OpenCode 内部数据的 IDEA 业务库，保证 Case 可迭代、Run 不覆盖、输入和配置快照不可篡改。
- 实现：新增 SQLite v1 Schema 及初始化器，建立设计文档列出的 21 张核心表、外键、索引、WAL/忙等待配置和不可变触发器；提供 Case/Run 创建、查询、列表、状态更新和显式删除方法。
- 持久化语义：重新分析创建具有 `parent_run_id` 的新 Run；`run_inputs` 不允许 UPDATE；Run 的 Case、日期、模型、Skill/Workflow 版本和配置快照不允许 UPDATE；删除只由显式方法触发并留存最小 `deletion_events` 记录。
- 涉及文件：`backend/idea/database.py`、`backend/tests/test_database.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_database backend.tests.test_config -v`，12 项全部通过；覆盖迁移、双 Run 历史、输入/配置不可变、状态可更新、Run 删除审计及 Case 级联删除；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-DB-001] add immutable case and run storage`
- 已知限制：运行目录中的报告/附件原子写入与文件删除由 `IDEA-RUNSTORE-001` 实现；严格状态迁移由 `IDEA-WF-001` 实现。

## 2026-07-16 — IDEA-RUNSTORE-001

- 类型：Harness 耐久 Run Store
- 目标：为每次 IDEA Run 保存不受缓存清理影响的输入、附件、结构化报告、Markdown 报告和可校验 manifest。
- 实现：新增 Case/Run 安全路径布局、原子字节/文本/JSON 写入、附件快照、SHA-256 文件清单、完整性复验与显式 Run/Case 目录删除。
- Harness 门禁：未存在 `input/input.json` 不得写入完成报告；`manifest.json` 必须最后原子落盘；校验时逐文件比对大小和哈希；阻止路径穿越和重名附件。
- 涉及文件：`backend/idea/run_store.py`、`backend/tests/test_run_store.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_run_store backend.tests.test_database backend.tests.test_config -v`，18 项全部通过；新增 6 项覆盖完整 Run、篡改检测、输入门禁、路径穿越、手动删除隔离和重名附件；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-RUNSTORE-001] add durable run manifests`
- 已知限制：报告内容及完成状态的业务校验将由 Workflow 和 Report Validator 完成。

## 2026-07-16 — IDEA-CACHE-001

- 类型：Harness FIFO 缓存
- 目标：建立默认 1 GiB 上限的可重建缓存，超限时严格按首次成功写入顺序清理，且不得触及持久 Run 结果。
- 实现：新增原子缓存写入、单调 `sequence` FIFO 索引、低水位回落、读取租约、强制清理、容量统计和启动修复；文件路径由类别与 key 哈希构造。
- FIFO 语义：读取不更新顺序；超限后按 `sequence ASC` 删除到低水位；租约中条目暂时跳过；单个对象大于容量上限时不入缓存；相同 key 的不同内容视为冲突并拒绝覆盖。
- 隔离保证：Cache Store 只接收 `storage.cache_dir`，测试同时建立独立历史文件并验证清理后仍存在。
- 涉及文件：`backend/idea/cache.py`、`backend/tests/test_cache.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_cache backend.tests.test_run_store backend.tests.test_database backend.tests.test_config -v`，25 项全部通过；新增 7 项覆盖 FIFO 顺序、读取不续期、租约、超大对象、key 冲突、孤儿修复和路径防护；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-CACHE-001] enforce FIFO cache capacity`
- 已知限制：生产配置的 1 GiB/0.9 GiB 阈值由已验证的统一配置注入；本单元测试用 10/6 字节缩小阈值验证边界。

## 2026-07-16 — IDEA-HEALTH-001

- 类型：Harness 启动接线与健康检查
- 目标：将统一配置、业务库和 FIFO 缓存接入 FastAPI 实际启动路径，并分组件报告系统是正常、降级还是核心错误。
- 实现：FastAPI 导入时严格加载配置、迁移 SQLite、初始化并修复缓存；新增 `/api/system/health`、`/api/system/config`、`/api/system/cache` 和手动缓存清理接口；原 `/api/health` 保留兼容并返回聚合状态。
- 健康维度：FastAPI、配置源、SQLite 读写、OpenCode 可执行文件、模型认证是否存在、EXA MCP 配置、本地 Google Patents 网络探测、缓存容量/可写性和 Workflow 恢复器。
- 降级语义：EXA 或 Google Patents 单路失败时仍允许核心系统工作并标记 `degraded`；模型认证、数据库、缓存或执行引擎失效时标记 `error`；响应永不返回 API Key。
- 涉及文件：`.gitignore`、`backend/idea/health.py`、`backend/main.py`、`backend/tests/test_health.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_health backend.tests.test_cache backend.tests.test_run_store backend.tests.test_database backend.tests.test_config -v`，30 项全部通过；通过 `main` 实际导入和缓存接口冒烟测试；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-HEALTH-001] expose component health and cache status`
- 已知限制：Workflow 恢复器在 `IDEA-WF-001` 前明确报告 `pending`；EXA 本单元只校验配置存在，真实调用状态由 Provider 工具调用审计记录。

## 2026-07-16 — IDEA-PROVIDER-001

- 类型：检索 Provider 契约与 Harness 调用门禁
- 目标：为 EXA、本地 Google Patents 及后续缓存降级建立相同的严格输入/输出契约，防止未真实执行、超时或返回结构错误的调用被记为成功。
- 实现：新增严格 `SearchQuery`、`FetchRequest`、`SearchHit`、`FetchedDocument`、`ProviderResult` 模型，抽象 `SearchProvider`，以及对搜索/抓取进行真实异步执行、超时和契约校验的 `ProviderRunner`。
- 状态语义：`SUCCESS`、`EMPTY`、`TIMEOUT`、`ERROR`、`CONTRACT_ERROR`、`DISABLED` 相互独立；只有真实返回且通过契约的结果才是成功；空结果是“成功调用但无命中”，不伪造文献。
- 契约校验：命中项必须是已验证模型、Provider 名必须匹配执行者、排名不得重复、结果不得超过请求上限、每项必须有公开号或可追溯 URL。
- 涉及文件：`backend/idea/providers/__init__.py`、`backend/idea/providers/base.py`、`backend/tests/test_provider_contract.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_provider_contract backend.tests.test_health backend.tests.test_cache backend.tests.test_run_store backend.tests.test_database backend.tests.test_config -v`，38 项全部通过；新增 8 项覆盖真实命中、空结果、超时、异常、Provider/排名契约、超量结果、全文抓取和显式禁用；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-PROVIDER-001] define audited search contracts`
- 已知限制：具体 HTTP/MCP 调用将在 `IDEA-GPAT-001`、`IDEA-GPAT-002` 和 `IDEA-EXA-001` 接入本契约。

## 2026-07-16 — IDEA-GPAT-001

- 类型：本地 Google Patents 搜索 Provider
- 目标：不经 EXA MCP，在本地构造 Google Patents 查询、解析分页搜索结果并返回统一 Provider 命中，以消除单一远程 MCP 依赖。
- 实现：新增 Google Patents 查询 URL 编码、分页/数量/国家参数、可容错 HTML Parser、公开号链接回退提取、请求限速、指数重试、环境代理失败后直连降级和 FIFO 响应缓存。
- 配置变更：统一配置和 Schema 新增 `trust_environment_proxy` 与 `fallback_to_direct`；依赖声明更改为 `httpx[socks]`，使有效 SOCKS 环境可直接使用，未安装 SOCKS 支持时仍能回退直连。
- 首轮测试：1 项失败；公开号同时从链接和页面字段提取后被拼接两次。
- 修正：页面显式结构化字段覆盖链接推导值，链接只在页面字段缺失时回退。
- 离线测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_google_patents_search ... -v`，43 项全部通过；新增 5 项覆盖真实结构 fixture、URL/分页、数量上限、响应缓存和断网错误；配置/Schema JSON 与 `git diff --check` 通过。
- 在线冒烟测试：实际调用返回 `ERROR / ConnectTimeout / 0 hits`；当前机器的 SOCKS 环境代理端口拒绝连接，回退直连也在 5 秒内超时。系统正确保留故障而未伪报成功。
- 涉及文件：`config/ai4patent.json`、`config/ai4patent.schema.json`、`backend/requirements.txt`、`backend/idea/config.py`、`backend/idea/providers/google_patents.py`、`backend/idea/providers/__init__.py`、`backend/tests/fixtures/google_patents_search.html`、`backend/tests/test_google_patents_search.py`、`docs/development-log.md`。
- 提交主题：`feat(idea): [IDEA-GPAT-001] add local Google Patents search`
- 已知限制：当前部署环境需要可用的外网直连或代理才能获得实时 Google 命中；断网时使用已缓存响应或后续 EXA Provider。

## 2026-07-16 — IDEA-GPAT-002

- 类型：本地 Google Patents 全文抓取与解析
- 目标：按公开号直接抓取 Google Patents 详情页，提取可供摘要筛选、独权核验和证据定位的结构化全文。
- 实现：新增公开号规范 URL 构造、详情页 HTML Parser、DC/itemprop 双源元数据提取、发明人/申请人/日期、摘要、逐项权利要求、逐段说明书及 `start/end/text/label` 证据 span；全文响应进入 FIFO `documents` 缓存。
- 门禁：页面没有公开号或没有任何专利文本章节时返回 `CONTRACT_ERROR`；请求公开号与页面公开号不同时返回 `CONTRACT_ERROR`；不允许用链接或模型推测的正文冒充已抓取内容。
- 解析容错：支持页面显式 claim/description block，也保留整节 itemprop 回退；正确处理 HTML 空元素，避免 `<meta>`/`<br>` 破坏章节深度计算。
- 涉及文件：`backend/idea/providers/base.py`、`backend/idea/providers/google_patents.py`、`backend/idea/providers/__init__.py`、`backend/tests/fixtures/google_patent_detail.html`、`backend/tests/test_google_patents_fetch.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_google_patents_fetch ... -v`，48 项全部通过；新增 5 项覆盖元数据/全文/span、公开号 URL、文档缓存、非法页面和公开号不匹配；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-GPAT-002] parse patent full text and evidence spans`
- 已知限制：实时抓取与搜索共用 `IDEA-GPAT-001` 记录的外网限制；页面结构变化会显式进入契约错误并需要更新 fixture/Parser。

## 2026-07-16 — IDEA-EXA-001

- 类型：EXA MCP 受控 Provider
- 目标：将 EXA 从“模型可自行决定是否调用”改为后端显式执行、契约校验和缓存的检索 Provider。
- 实现：新增 Streamable HTTP MCP 客户端，完整执行 `initialize`、`notifications/initialized`、`tools/call`，携带协议版本和 session header，并支持 JSON/SSE 响应解码、JSON-RPC id/error 校验、`isError` 门禁与环境代理失败后直连。
- Provider 适配：支持 EXA `structuredContent`、JSON 文本与 Markdown 回退结果；专利检索自动加 Google Patents 定向词；公开号从专利 URL 可追溯提取；搜索/抓取工具响应进入 FIFO 缓存。
- 统一配置：EXA 新增 `endpoint`、`search_tool`、`fetch_tool`，后端不再从超长 Skill 文本推测工具名。
- 离线测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_exa_provider ... -v`，55 项全部通过；新增 7 项覆盖 MCP 三步握手/session、SSE、结构化去重、JSON 文本、抓取回退、MCP 故障和缓存；配置/Schema JSON 与 `git diff --check` 通过。
- 在线冒烟测试：对真实 `https://mcp.exa.ai/mcp` 执行检索，返回 `SUCCESS / 3 hits / no error`；未输出或记录响应全文。
- 涉及文件：`config/ai4patent.json`、`config/ai4patent.schema.json`、`backend/idea/config.py`、`backend/idea/providers/exa.py`、`backend/idea/providers/__init__.py`、`backend/tests/test_exa_provider.py`、`docs/development-log.md`。
- 提交主题：`feat(idea): [IDEA-EXA-001] add audited EXA MCP provider`
- 已知限制：EXA 抓取返回的非结构化文本只能作为降级全文，章节精确度低于本地 Google HTML Parser，报告必须标注 `structured_sections=false`。

## 2026-07-16 — IDEA-MERGE-001

- 类型：双路检索合并、去重与溯源
- 目标：将 EXA 与本地 Google Patents 的命中确定性合并，减少同一文献/同族重复深读，同时不因模糊相似度误删独立文献。
- 实现：新增公开号、申请号和同族号规范化；使用并查集按多标识传递合并；输出标准化 `MergedHit`、所有来源、Provider 排名、查询 ID、URL 及原始命中数据。
- 去重优先级：标准化公开号→标准化申请号→已知同族 ID→URL；标题+优先权日+申请人相似只生成 `possible_family_keys`，不自动合并。
- 字段融合：标题/摘要选择信息更完整的值；公开号、申请号和同族保留标准化标识；不覆盖任何原始 Provider 记录。
- 涉及文件：`backend/idea/merge.py`、`backend/tests/test_merge.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_merge ... -v`，61 项全部通过；新增 6 项覆盖编号规范化、双 Provider 同公开号、A1/B2 同申请、跨国已知同族、模糊同族不误合并和多查询溯源；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-MERGE-001] merge and deduplicate provider hits`
- 已知限制：没有 Provider 明确同族 ID 时，模糊同族只标记待确认；不为节省分析量而强行合并。

## 2026-07-16 — IDEA-SEARCH-001

- 类型：自适应检索预算、饱和停止与摘要筛选
- 目标：使宽泛范围和细分范围使用不同检索/深读规模，同时将用户上限、最低深读目标和停止原因变成确定性算法。
- 实现：新增 `narrow/medium/broad` 范围评估，依据技术领域数、必要特征数和检索词具体度构建预算；支持 quick/standard/deep 与用户自定义上限；新增连续轮次饱和跟踪和摘要/日期确定性初筛。
- 停止原因：`SATURATED`、`CANDIDATE_MAX`、`QUERY_EXHAUSTED`、`PROVIDERS_UNAVAILABLE`；用户上限和 Provider 全失败不得伪装成检索饱和。
- 深读语义：细分范围取 `deep_review_min`，宽范围逐步增加到 `deep_review_max`；下限始终不得低于 10；只选入评估日之前且达到相关性门槛的文献，不足时返回 `INSUFFICIENT_RELEVANT_DEEP_REVIEWS` 而不用弱相关结果凑数。
- 涉及文件：`backend/idea/search_strategy.py`、`backend/tests/test_search_strategy.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_search_strategy ... -v`，67 项全部通过；新增 6 项覆盖细分/宽泛预算、用户上限校验、连续饱和、上限/Provider 停止原因和不凑弱相关文献；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-SEARCH-001] add adaptive search budgets`
- 已知限制：当前相关性初筛是可解释的词项覆盖分；后续 Document Analyzer 会对入选文献的摘要和独立权利要求作第二层语义核验。

## 2026-07-16 — IDEA-WF-001

- 类型：Harness Workflow 状态机、恢复与完成门禁
- 目标：由后端持久状态机强制 IDEA 的 11 个步骤顺序、尝试次数、失败、取消、重启恢复和完成条件，不再依赖模型自称已执行。
- 实现：新增 11 个 `WorkflowStep`、Run 合法状态迁移、顺序步骤开始/成功/失败、输入输出哈希、持久 attempt、进度快照、取消和启动恢复；FastAPI 启动时实际运行恢复器。
- 恢复语义：服务重启时把未结束步骤标记 `INTERRUPTED / PROCESS_RESTART`，保留原 attempt，后续从同一步的新 attempt 重试；不从头重跑已成功步骤。
- 完成门禁：所有 11 步的最新 attempt 必须 `SUCCEEDED`；不存在 critical audit；Run Store manifest 及所有引用文件的大小/哈希必须通过复验；否则拒绝 `COMPLETED`。
- 涉及文件：`backend/idea/workflow.py`、`backend/main.py`、`backend/tests/test_workflow.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_workflow ... -v`，74 项全部通过；新增 7 项覆盖乱序阻止、重试上限、重启恢复、manifest 门禁、critical audit、取消终态和数据库进度；实际导入 `main` 验证健康检查已报告 recovery `ready`；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-WF-001] enforce resumable workflow states`
- 已知限制：本 Work Unit 提供状态与门禁；各步的具体 Agent/Provider 执行器和后台调度在后续 Work Unit 挂载。

## 2026-07-16 — IDEA-SCHEMA-001

- 类型：受限 Agent 结构化输出 Schema
- 目标：为 7 类 IDEA Agent 建立 fail-closed JSON 契约，使模型的自然语言声称在进入数据库和后续结论前必须经过程序校验。
- 实现：新增 Idea Parser、Query Planner、Document Analyzer、Inventive Step、Value Analyzer、Evidence Auditor 和 Report Composer 的严格 Pydantic 模型、Agent 注册表、统一验证入口与 JSON Schema 导出。
- 证据门禁：`DISCLOSED/PARTIAL` 特征映射必须有 evidence ID；显式特征必须有用户输入 source span；文献内特征 ID 不得重复。
- 结论门禁：`NOT_NOVEL` 必须且只能有一篇单独覆盖全部特征的破坏性文献；`NOVEL` 允许直接输出但必须列明缺失特征；`NOT_INVENTIVE` 必须对每个区别特征有 D2、evidence 和组合动机，否则只能返回 `NEED_MORE_EVIDENCE/UNCERTAIN`。
- 涉及文件：`backend/idea/agent_schemas.py`、`backend/tests/test_agent_schemas.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_agent_schemas ... -v`，80 项全部通过；新增 6 项覆盖披露证据、特征唯一、单篇新颖性、直接“具备新颖性”、创造性 D2 证据和 Agent 注册；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-SCHEMA-001] validate agent evidence outputs`
- 已知限制：Schema 保证结构和逻辑下限；evidence ID 是否真实存在、是否指向原文将由后续 Evidence Auditor 与数据库校验。

## 2026-07-16 — IDEA-MODEL-001

- 类型：DeepSeek 受控结构化模型客户端
- 目标：不借助通用对话 Agent 的自由流程，直接对每个受限 Agent 发送最小上下文与对应 JSON Schema，并且只返回经 Pydantic 校验的结果。
- 实现：新增 OpenAI-compatible `/chat/completions` 客户端、`json_object` 响应约束、Agent Schema 注入、JSON/固定 Markdown fence 解析、结构错误反馈重试、token/attempt/耗时元数据和环境代理失败后直连。
- 密钥处理：优先从 `DEEPSEEK_API_KEY` 读取，否则从被 Git 忽略的本地 auth 文件读取；返回值、错误、配置快照和开发日志不包含密钥。
- 统一配置：新增 `max_output_tokens=8192`；已通过真实 `/models` 接口验证当前账号提供 `deepseek-v4-flash` 和 `deepseek-v4-pro`，本系统按用户指定使用前者。
- 离线测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_model_client ... -v`，85 项全部通过；新增 5 项覆盖一次成功、无效 JSON 重试、Schema 耗尽且不泄密、fence 容错和缺失认证；配置/Schema JSON 与 `git diff --check` 通过。
- 在线冒烟测试：用真实 `deepseek-v4-flash` 对一段 KV Cache 方案执行 `patent-idea-parser`，第 1 次通过 Schema，返回 4 个特征，用量 682 prompt + 3957 completion = 4639 tokens；未记录模型输出全文。
- 涉及文件：`config/ai4patent.json`、`config/ai4patent.schema.json`、`backend/idea/config.py`、`backend/idea/model_client.py`、`backend/tests/test_model_client.py`、`docs/development-log.md`。
- 提交主题：`feat(idea): [IDEA-MODEL-001] add structured DeepSeek client`
- 已知限制：结构重试会产生额外 token 费用；工作流需通过最小上下文和文献级并发控制费用。

## 2026-07-16 — IDEA-PARSE-001

- 类型：IDEA Parser 与 Query Planner Agent 接入
- 目标：将 IDEA 解析和检索规划变成可审计、可持久且可二次校验的受限 Agent 步骤，不允许模型一步跳到检索结论。
- 实现：新增精简 Parser/Planner 系统提示、Agent Service、F1–Fn 持久化、Q1–Qn 持久化、Run 内全局唯一内部 ID 和模型调用审计记录。
- 二次门禁：所有 `explicit` 特征的 `start/end/text` 必须与用户输入逐字一致；越界或不匹配立即失败；检索式中出现括号占位符、`TODO/TBD` 等标记时拒绝持久化。
- 日志隐私：`tool_calls.request_json` 只记录 Agent 名和输入字符数，不记录完整 IDEA；响应摘要只记录 attempt、usage 和 response ID。
- 涉及文件：`backend/idea/agents.py`、`backend/tests/test_agents.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_agents ... -v`，89 项全部通过；新增 4 项覆盖特征持久化/脱敏审计、span 不匹配、Run 内查询 ID 和占位检索式；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-PARSE-001] persist validated idea and query plans`
- 已知限制：语义正确性仍由固定 Eval 案例衡量；本单元保证的是来源可追溯、结构合法且不能越过检索步骤。

## 2026-07-16 — IDEA-RETRIEVE-001

- 类型：双路并行检索、自适应筛选与全文抓取编排
- 目标：将 Query Plan、EXA、本地 Google Patents、去重、摘要/日期筛选、饱和停止和全文降级连成可审计的真实检索执行层。
- 实现：每轮对每个 Q 并行调用所有 Provider，逐调用持久状态/耗时/命中数/错误，逐命中持久原始来源；每轮重新合并、筛选并计算新独立家族/高相关家族。
- 全文策略：对入选深读公开号使用有界并发；优先本地 Google 结构化全文，失败后用 EXA 文本降级；两路都失败保留逐文献 limitation；成功文桮持久到 `patent_documents/run_documents`。
- 故障语义：单路全失败生成 `PROVIDER_DEGRADED` 但继续；所有 Provider 失败停止为 `PROVIDERS_UNAVAILABLE`；全文成功数低于 10 生成 `DEEP_REVIEW_FETCHED_BELOW_MINIMUM`。
- 涉及文件：`backend/idea/retrieval.py`、`backend/tests/test_retrieval.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_retrieval ... -v`，93 项全部通过；新增 4 项覆盖双 Provider 并行/去重/持久化、单路降级、全路失败和 10 篇全文本地失败→备路成功。`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-RETRIEVE-001] orchestrate dual-provider retrieval`
- 已知限制：当前全文抓取是“本地优先、EXA 备用”而不是对同一文献重复抓两份，以控制网络和 token 成本；搜索命中仍是两路并行合并。

## 2026-07-16 — IDEA-DOC-001

- 类型：文献级语义分析、证据包与长期/缓存数据分层
- 目标：只让 Document Analyzer 读取摘要、独立权利要求和高相关说明书片段，并强制所有披露判断引用后端生成的真实 evidence ID。
- 实现：新增确定性 evidence packet 构造器，逐段校验原文 offset/text、按权利要求→摘要→相关说明书排序并限制总字符；文献级 Agent 使用有界并发且只接收单篇文献和 F1–Fn，不允许跨文献拼接或直接判断整体新颖性。
- 证据门禁：输出必须逐一映射全部必要特征；公开号必须匹配；`DISCLOSED/PARTIAL` 只能引用本次 evidence packet 内的 ID；未知 ID、缺失特征或重复分析均 fail closed。
- 数据分层：成功分析后，SQLite 长期保留文献元数据、全文哈希、原文引文、offset 和特征映射；摘要/权利要求/说明书全文以及 metadata 中的全文 spans 从业务库释放，完整 Provider 响应仍由 1 GiB/FIFO 可重建缓存管理。
- 涉及文件：`backend/idea/agents.py`、`backend/idea/document_analysis.py`、`backend/tests/test_document_analysis.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_document_analysis ... -v`，96 项全部通过；新增 3 项覆盖真实 span/evidence 持久化与全文释放、伪造 evidence ID 拒绝、必要特征映射缺失拒绝；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-DOC-001] analyze documents with evidence packets`
- 已知限制：当前说明书片段排序使用可解释的词项覆盖率；复杂同义表达可能降分，但摘要和权利要求始终进入证据包，且后续 Eval 会衡量召回率。

## 2026-07-16 — IDEA-NOVELTY-001

- 类型：确定性新颖性矩阵与直接结论
- 目标：从持久化的逐篇 F1–Fn 映射生成新颖性裁决，保证“同一篇文献覆盖全部必要特征”由程序执行，而不是由模型自行声称。
- 实现：新增 Novelty Service，按每篇深读文献构造独立矩阵、计算最接近文献、识别所有各自独立的破坏性文献并选定主引用；明确支持输出 `NOVEL / 具备新颖性`，同时携带理由、置信度、缺失特征和局限性。
- 完成门槛：无单篇破坏文献时，只有深读数量达到配置下限且每篇均存在明确 `NOT_DISCLOSED` 缺口，才能输出 `NOVEL`；不足 10 篇或只存在 `PARTIAL/UNCERTAIN` 缺口时输出 `UNCERTAIN`，不伪造肯定结论；已有单篇完整披露时即使数量不足仍可输出 `NOT_NOVEL`。
- 审计门禁：重新核对 evidence ID 必须属于同一 Run/文献、quote 哈希一致、全部必要特征均有且仅有一个映射、文献公开日在评估日之前；任一失败即 fail closed。
- Schema 修正：现实中可有多篇文献各自独立破坏新颖性，因此从“必须恰好一篇”调整为“至少一篇，且主引用必须来自破坏性矩阵”；仍严禁跨文献拼接。
- 涉及文件：`backend/idea/agent_schemas.py`、`backend/idea/novelty.py`、`backend/tests/test_agent_schemas.py`、`backend/tests/test_novelty.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v`，103 项全部通过；新增 7 项覆盖单篇原则、10 篇直接新颖结论、数量不足降级、多破坏文献、证据哈希篡改、评估日后文献和 Schema 多文献语义；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-NOVELTY-001] enforce single-document novelty decisions`
- 已知限制：当前置信度由已验证映射确定性聚合，不代表检索空间的统计覆盖概率；报告必须同时展示检索范围、Provider 状态和停止原因。

## 2026-07-16 — IDEA-INVENT-001

- 类型：多 D1 创造性路线与受限并发 Agent
- 目标：把程序处理和语义判断拆开：后端选择多个最接近 D1、准备区别特征和 D2 证据，多个 Agent 实例并发分析各自路线，但不得自行搜索或编造证据。
- 实现：按单篇已披露特征数和映射置信度选择最多 3 条 D1 路线；每个区别特征只注入其他深读文献中已持久化的 `DISCLOSED/PARTIAL` D2 候选和原文引文；所有路线先全部通过契约再单事务持久化，避免部分成功留下半套结果。
- 结论门禁：Route ID、D1、公开号、区别特征集合必须精确匹配；D2 公开号和 evidence ID 必须来自该区别特征的候选且逐项绑定；`NOT_INVENTIVE` 要求每个区别特征至少有一个完全 `DISCLOSED` 的 D2 教导、有引文且有组合动机，只有部分披露时强制拒绝。
- 短路语义：新颖性已被单篇文献破坏时不再消耗模型调用分析创造性，创造性步骤以“不适用”空路线正常结束。
- 涉及文件：`backend/idea/inventiveness.py`、`backend/tests/test_inventiveness.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，107 项全部通过；新增 4 项覆盖多 D1 并发和原子持久化、伪造 evidence 拒绝、不新颖短路、仅部分披露 D2 不得得出不具创造性；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-INVENT-001] analyze bounded multi-D1 routes`
- 已知限制：当前每个区别特征最多注入 5 个 D2 候选以控制 token；候选耗尽或组合动机证据不足时必须返回 `NEED_MORE_EVIDENCE/UNCERTAIN`，不会扩展为 Agent 自主搜索。

## 2026-07-16 — IDEA-VALUE-001

- 类型：冻结结论后的价值预评估
- 目标：在不重新解释检索证据、不修改新颖性/创造性结论的前提下，评估可取证性、可规避性、技术/市场价值和申请策略。
- 实现：Value Agent 仅接收 IDEA 特征/效果、冻结的新颖性摘要和创造性路线摘要；不注入全文或原始 evidence packet；输出 detectability、workaround difficulty、technical/market value、至少两条替代路径和申请建议并持久化。
- 依据门禁：后端为输入分配 `IDEA:* / EFFECT:* / NOVELTY:CONCLUSION / INVENTIVE:*` basis ID；三个价值维度均必须引用至少一个已提供 basis ID，未知/自创 ID 或占位替代路径立即拒绝。
- 数据边界：本步骤是专利价值预评估而非独立市场尽调；Prompt 明令不得搜索或编造市场事实，输出必须在 limitations 中反映不确定性。
- 涉及文件：`backend/idea/value_analysis.py`、`backend/tests/test_value_analysis.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，111 项全部通过；新增 4 项覆盖最小冻结上下文和持久化、未知 basis、占位替代路径、维度缺少依据；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-VALUE-001] assess value from frozen conclusions`
- 已知限制：没有外部市场数据源时，`technical_market_value` 仅代表基于用户方案和专利分析的初步判断，正式商业决策仍需另行尽调。

## 2026-07-16 — IDEA-AUDIT-001

- 类型：确定性证据审计与受限语义审计
- 目标：阻止模型仅声称“已核验”却未实际检查，并确保只有可复验的程序错误可以成为阻断 Workflow 完成的 critical。
- 确定性审计：检查深读数量、评估日/公开日、每篇 F1–Fn 完整性、披露映射是否有证据、evidence 的 Run/文献归属与 SHA-256、数据库映射与新颖性矩阵逐项一致、内存与持久化的新颖性/创造性/价值结果一致。
- 语义审计：Evidence Auditor 只读取冻结结果和去重后的原文引文 inventory；必须精确回传全部 evidence ID 和公开号，少一项、重复项或自创 ID 均拒绝整步持久化。
- 失控隔离：程序发现的日期/哈希/一致性问题可写入 `critical` 并阻止完成；模型报告的 `critical` 降为带原始级别记录的 `warning`，可提示人工复核但不能凭模型主观判断让系统失控。
- 涉及文件：`backend/idea/audit.py`、`backend/tests/test_audit.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，115 项全部通过；新增 4 项覆盖完整 inventory 证明、干净审计、哈希篡改程序阻断且不调用模型、模型 critical 降级；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-AUDIT-001] enforce deterministic evidence audits`
- 已知限制：语义审计仍是模型意见，不替代专利代理师复核；因此它只产生 advisory finding，最终硬门禁基于日期、归属、哈希和矩阵一致性。

## 2026-07-16 — IDEA-REPORT-001

- 类型：权威结构化报告、Markdown 和完整性 Manifest
- 目标：让程序而非模型掌握最终专利号、日期、统计和结论，同时生成内部可读且可历史复验的完整输出。
- 实现：Report Service 从数据库加载检索计划/调用/命中/Provider 状态/深读文献和特征映射，与冻结的新颖性、创造性、价值和审计对象组装 `report.json`；确定性渲染含 14 个固定章节的 `report.md`；Run Store 原子写入两份报告并最后写 `manifest.json`。
- 叙述边界：Report Composer 只生成摘要、模拟审查意见和行动建议；新颖性中文标签由程序固定。叙述必须以固定标签开头，后文不得偷换为相反/不确定结论，不得引入未知专利公开号；失败时任何报告文件均不落盘。
- 完整性：Manifest 记录输入快照、JSON 和 Markdown 的大小/SHA-256；`reports` 表保存实际路径和两份报告哈希；已有报告的 Run 禁止覆盖。
- 涉及文件：`backend/idea/reporting.py`、`backend/tests/test_reporting.py`、`docs/development-log.md`。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，119 项全部通过；新增 4 项覆盖 14 节报告/Manifest/数据库哈希、直接矛盾标签、未知专利号、正确前缀后偷换结论；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-REPORT-001] generate authoritative audited reports`
- 已知限制：模拟审查意见是基于已审计结构化事实的辅助文本，不是实际官方审查意见；权威数据以 `report.json` 为准。

## 2026-07-16 — IDEA-CKPT-001

- 类型：Workflow 写一次结构化检查点与设计补强
- 目标：关闭“业务结果已经持久化、Harness 尚未写步骤成功，进程恰好退出”这一恢复窗口，避免恢复时重复调用模型或生成不一致结果。
- 实现：数据库 schema 升级为 v2，新增 `stage_results(run_id, stage_name)`，保存 canonical JSON、SHA-256 和时间；同内容重复写幂等，不同内容覆盖被拒绝，读取时重新计算哈希。
- 原子边界：Idea Parser 和 Query Planner 的完整输出检查点与 `idea_features/search_queries` 在同一个 SQLite 事务写入；恢复器可先复验检查点并重建强类型对象，无需加载 OpenCode 全上下文或再次请求模型。
- 文档变更：在 Harness 核心规则中加入跨步骤结构化结果必须 write-once 检查点、可同事务时必须同事务的要求；属于正式执行器实现前发现的必要恢复约束。
- 涉及文件：`backend/idea/database.py`、`backend/idea/agents.py`、`backend/tests/test_database.py`、`backend/tests/test_agents.py`、`docs/idea-rebuild-technical-design.md`、`docs/development-log.md`。
- 测试：Parser/Planner/数据库定向 11 项全部通过；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，120 项全部通过；新增写一次/哈希/幂等和两个 Agent 同事务检查点断言；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-CKPT-001] add write-once stage checkpoints`
- 已知限制：Provider 网络调用无法与 SQLite 事务原子提交；执行器会在缺少完成检查点时清理该步骤的临时调用/命中记录后按固定 request 重新执行，且不会把中间状态标记为成功。

## 2026-07-16 — IDEA-WF-002

- 类型：固定 11 步端到端执行器、局部重试与恢复
- 目标：把所有已实现服务串成后端强制 Workflow，模型不能选择、跳过或伪造步骤；失败、超时、恢复、审计和 Manifest 决定真实终态。
- 实现：新增 Workflow Executor，依次执行输入快照、解析、模型校验、查询规划、检索、全文抓取、文献分析、新颖性、创造性、价值、审计与报告；每步使用 Harness attempt/timeout/output hash 和 write-once stage checkpoint。
- 恢复语义：Parser/Planner 从原子检查点恢复；检索缺少完成检查点时只清理该步临时 tool_calls/search_hits 后重试；全文抓取重试只清理未分析 Run 关联并重新回填共享文献的临时全文；文献分析按 `deep_reviewed=0` 继续剩余文献；新颖性/创造性/价值/审计/报告从各自持久结果恢复。
- 完成门禁：critical 审计在报告前阻断并按 attempt 上限进入 FAILED；报告完成后再次复验 11 步、audit_results 和 Manifest 全文件哈希；最终门禁失败明确写入 `COMPLETION_GATE_FAILED`，不得返回完成。
- 必要特征修正：`required=false` 的可选/推断特征保留在 IDEA 和报告中，但不进入 Document Analyzer 必要映射、新颖性单篇覆盖和审计完整性计数。
- 涉及文件：`backend/idea/execution.py`、`backend/idea/retrieval.py`、`backend/idea/novelty.py`、`backend/idea/audit.py`、`backend/tests/test_execution.py`、`backend/tests/test_novelty.py`、`backend/tests/test_audit.py`、`docs/development-log.md`。
- 测试：定向 19 项全部通过；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，125 项全部通过；新增 5 个执行器场景覆盖固定图、单步重试、崩溃窗口检查点、critical 审计、Manifest 篡改，另覆盖可选特征；`compileall` 与 `git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-WF-002] execute the fixed review workflow`
- 已知限制：本单元提供执行器本体；后台 Task 管理、SSE/API 和启动时自动重新调度在后续 API Work Unit 接入。

## 2026-07-16 — IDEA-API-001

- 类型：生产 Runtime、后台任务管理与 IDEA API
- 目标：让前端和后续 Skill 只调用受控 Workflow API，不再把完整流程交给 OpenCode 对话自行执行。
- Runtime：统一装配 SQLite、1 GiB FIFO Cache、Run Store、Harness、DeepSeek 结构化客户端、本地 Google Patents、EXA MCP、7 类 Agent Service 和 11 步 Executor；启动时恢复中断 attempt 并重新调度 QUEUED/RUNNING Run。
- API：实现 Case 新建/列表/详情，Run 新建/详情/SSE/取消/重跑，报告 JSON/Markdown、产物列表，以及 Run/Case 手动删除；重跑创建带 parent_run_id 的新不可变历史，不覆盖旧结果。
- 输入与设置门禁：IDEA 长度、评估日、仅 full scope、quick/standard/deep、候选上限和深读上下限均由严格 Schema 校验；自定义组合与模式默认值不一致时在创建 Run 前 422；附件名限制在配置的 uploads 目录且防路径穿越。
- 状态与完整性：后台同一 Run 去重启动；取消进入持久 CANCELLED；SSE 只轮询持久 Harness 状态并在终态结束；报告 API 返回前复验 Manifest；用户配置的深读下限同时传给抓取 limitation 和新颖性肯定结论门禁。
- 涉及文件：`backend/idea/runtime.py`、`backend/idea/api.py`、`backend/main.py`、`backend/idea/execution.py`、`backend/idea/retrieval.py`、`backend/idea/novelty.py`、`backend/tests/test_api.py`、`backend/tests/test_execution.py`、`docs/development-log.md`。
- 测试：API/Task 定向 8 项全部通过；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，133 项全部通过；主应用导入/OpenAPI/双 Provider 装配冒烟通过；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-API-001] expose durable workflow APIs`
- 已知限制：当前附件沿用内部上传目录；MVP 前端先提供纯文本 IDEA 和检索参数，附件选择可在后续 UI 迭代显式展示。

## 2026-07-16 — IDEA-UI-001

- 类型：仅 IDEA 三栏前端、历史与结果可视化
- 目标：移除尚未实现的五模块导航和通用 Agent 会话入口，让内部用户只通过受控 IDEA Workflow 新建、查看和管理完整评估历史。
- 布局：左栏为共享 Case/Run 历史和终态标识；中栏为 IDEA、评估日、日期依据、quick/standard/deep、候选上限、深读上下限及 11 步持久进度；右栏为结论卡和总览/特征/检索文献/新颖性/创造性/价值/审计限制标签页。
- 交互：支持新建 Case/Run、SSE 断线可恢复进度、取消、重跑、Run/Case 手动删除、历史报告恢复和 Markdown 导出；刷新页面后从本地 API 重建，不依赖浏览器 session 或 OpenCode 对话上下文。
- 显示安全：所有 API 文本通过 DOM `textContent` 渲染，不使用 `innerHTML` 注入外部专利内容；报告结论取自权威 `report.json`；未实现模块和 `/api/run` 完全不在新前端出现。
- 健康状态：页面展示核心/降级状态和 FIFO 缓存使用量；Google Patents 健康探针与 Provider 一致支持环境代理失败后直连。当前主机 Google 直连超时会诚实显示降级，EXA/模型/缓存/Workflow 仍可工作。
- 涉及文件：`frontend/index.html`、`frontend/style.css`、`frontend/app.js`、`backend/idea/health.py`、`backend/tests/test_frontend.py`、`backend/tests/test_health.py`、`docs/development-log.md`。
- 测试：前端/健康定向 10 项通过；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，138 项全部通过；QuickJS 完整脚本解析通过；实际 Uvicorn 启动并请求 HTML/CSS/JS/OpenAPI/health 冒烟通过；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-UI-001] replace frontend with IDEA workspace`
- 已知限制：当前执行环境没有 Chromium，未执行像素级浏览器截图回归；已完成响应式 CSS 契约、DOM ID、JS 语法、静态资源和真实 HTTP 加载验证。

## 2026-07-16 — IDEA-SKILL-001

- 类型：薄 Skill、确定性 CLI 与 OpenCode 强制路由
- 目标：把原 1100 余行“写给模型看的操作手册”替换为只负责输入归一化、调用本地受控 Workflow、等待持久终态和展示权威报告的薄入口，消除模型自行选择、跳过或声称已执行步骤的空间。
- Skill：新增 103 行 `patent-idea-review/SKILL.md`，将 API、预算、证据结论和报告字段拆入 4 份按需 references；新增无第三方依赖的 `idea_workflow.py`，支持 health/history/start/status/wait/report/cancel/run，输入或预算无效时不创建孤儿 Case。
- 路由：重写 `AGENTS.md` 的 IDEA 强制规则，取消全局 EXA-only 假设；IDEA 只能装载新 Skill 并调用 Workflow，未获得成功终态及 Manifest 验证报告时不得声称完成。本地 Google Patents 与 EXA 由后端并行、独立留痕、合并去重和降级。
- 迁移：原 `patent-IDEA-analyzer` 保留完整正文并标为 DEPRECATED/DO NOT LOAD，仅作行为回归和后续资料迁移；Skills README 将当前产品入口、直接“具备新颖性”的证据限制和双 Provider 故障语义写清。
- 涉及文件：`config/opencode/AGENTS.md`、`config/opencode/skills/README.md`、`config/opencode/skills/patent-IDEA-analyzer/SKILL.md`、`config/opencode/skills/patent-idea-review/`、`backend/tests/test_skill_cli.py`、`docs/development-log.md`。
- 测试：Skill CLI 5 项合约测试全部通过；Skill Creator `quick_validate.py` 通过；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，143 项全部通过；实际 Uvicorn 下执行 health/history 冒烟通过；`git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-SKILL-001] route IDEA skill through workflow`
- 已知限制：当前主机访问 Google Patents 直连仍可能超时并显示 degraded；这不会被伪装成成功，本地缓存和 EXA 备路仍由后端按真实状态工作。完整在线 Run 在下一工作单元验证。

## 2026-07-16 — IDEA-E2E-001

- 类型：真实模型/Provider E2E、EXA 契约加固、筛选与部署收口
- 目标：使用真实 DeepSeek、EXA MCP、本地 Google Patents、固定 11 步 Workflow 和薄 Skill 完成至少 10 篇全文深读的权威报告，并把在线验证暴露的问题转为程序约束和回归测试。
- 在线发现与修复：EXA 已移除 `crawling_exa`，改用当前 `web_fetch_exa` 的 `urls[] + maxCharacters` 契约；全文预算统一配置为 300000；新增 Google Patents Markdown 结构解析，兼容标题紧贴、字段无空格和超长页面，提取 Info/摘要/权利要求/说明书及可定位 spans。
- 检索漏斗：摘要阶段改用 Query Planner 的中英双语概念组，不再用中文长特征做整句包含；具体概念命中可进入全文核验，无概念命中仍不凑数。全文少于用户下限时在抓取步骤强制失败，不得进入新颖性和报告。
- 降级性能：依据本 Run 检索调用的真实成功/失败统计排列全文 Provider；检索全部失败的 Provider 降为备路，主路抓取失败时仍尝试，避免每篇重复等待已知超时。
- 失败样本留存：真实测试前四个 Run 分别暴露零篇筛选、仅 5 篇强筛选、CN 紧凑 Info 丢日期、超长 US 页面截断问题，均以 FAILED 终态保留，没有删除、覆盖或伪装为完成。
- 成功 E2E：Run `1e4020cb-9e28-4823-8b49-a46ce7a76c3a` 在 241895 ms 内达到 `COMPLETED_WITH_LIMITATIONS`；4 次 EXA 搜索返回 40 条、10 次 EXA 全文全部成功、10 篇全部深读、180 条 evidence、20 次结构化模型调用成功、11/11 步通过、critical=0。报告直接结论为 `NOVEL`，置信度 0.525；本地 Google 4 次检索超时作为唯一限制明确保存。
- 权威产物：`report.json` 343171 bytes、`report.md` 7551 bytes、`manifest.json` 810 bytes；通过报告 API Manifest 复验，Case/Run 历史和失败 Run 均可在刷新后恢复。
- 部署与文档：README 改为 IDEA-only 使用说明；IDEA 启动不再强制安装 OpenCode 引擎，旧能力可通过 `INSTALL_OPENCODE=1` 可选安装；启动就绪探针改查本地 OpenAPI，外部 Provider 健康不再造成服务启动误报；技术设计升级为 1.1，并记录“深读不足 10 必须失败”和真实 MCP 契约。
- 涉及文件：`backend/idea/config.py`、`backend/idea/execution.py`、`backend/idea/providers/exa.py`、`backend/idea/retrieval.py`、`backend/idea/search_strategy.py`、相关测试、`config/ai4patent.json`、Schema、`README.md`、`install.sh`、`start.sh`、`dev.sh`、技术设计和本日志。
- 测试：`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`，148 项全部通过；Skill quick validate、`compileall`、配置/Schema JSON、4 个 shell 脚本语法、`git diff --check` 全部通过；`start.sh → 首页/API 历史 → stop.sh` 真实部署冒烟通过且无残留服务。
- 提交主题：`feat(idea): [IDEA-E2E-001] harden live retrieval and finish IDEA`
- 已知限制：本机当前无法直连 Google Patents，因此成功 Run 为 EXA 单路在线降级状态；本地 Provider 已实现但不是离线镜像。FastAPI 测试仍提示 Starlette `httpx` 兼容层弃用警告，不影响本次 148 项结果，后续依赖升级需单独处理。

## 2026-07-16 — IDEA-TESTENV-001

- 类型：当前 Linux 环境复验、测试依赖修复、健康门禁与在线终态语义回归。
- 目标：依据 README、技术设计和追加日志重新验证整个 IDEA 项目，修复依赖升级后未完成的 API 测试、统一健康配置偏差，以及真实报告包含限制但 Run 误报 `COMPLETED` 的缺陷。
- 环境修复：当前虚拟环境为 Python 3.12.3、FastAPI 0.139.0、Starlette 1.3.1；新版 Starlette `TestClient` 需要 `httpx2`，旧 `httpx` 兼容层在受限沙箱内会卡死。`backend/requirements.txt` 新增 `httpx2>=2.0`，真实 Linux 环境中的 API 测试恢复正常；README 的 Skill 校验路径改为可移植 `CODEX_HOME`/用户目录形式。
- 健康修复：OpenCode 路径按 `OPENCODE_EXE`、系统 PATH、项目 Linux 路径解析，并作为 IDEA-only Runtime 的可选兼容组件，不再阻止核心 Workflow；EXA 健康检查直接读取 `config/ai4patent.json` 的统一 endpoint/tool 配置，不再依赖不存在且非权威的 `config/opencode/opencode.json`。
- 终态修复：`AUDIT_AND_REPORT` 写一次检查点现在保存权威报告的完整 limitations，完成门禁统一聚合 Provider、检索、创造性、价值和审计限制；新增回归断言，任何报告限制均使终态成为 `COMPLETED_WITH_LIMITATIONS` 并持久化到 `limitation_json`。
- 在线模型冒烟：使用忽略式本地密钥临时注入 `https://api.deepseek.com` / `deepseek-v4-flash`；Parser 第 1 次通过 Schema，2 个特征，3273 tokens；未打印密钥或模型全文。
- 首次发现性 E2E：Run `c47f9171-7f02-4c0f-b774-176e4fd4b1c8` 在 223046 ms 内完成 11/11 步，17 个候选、10 篇深读、EXA 3 次调用全成功、Google Patents 12 次调用中 1 次契约失败、critical=0；Manifest 报告可读，但旧进程把含 5 条限制的报告误记为 `COMPLETED`，该历史保留作为缺陷证据，未改写。
- 修复后 E2E：Run `e99f0257-123f-4c9c-bebf-600721faed97` 在 263587 ms 内达到 `COMPLETED_WITH_LIMITATIONS`；11/11 步均 attempt 1，40 条原始命中、26 个去重候选、10 篇深读，EXA 4/4、Google Patents 14/14 调用成功，critical=0、warning=1，6 条限制已同时写入报告和 Run 终态；`report` 命令完成 Manifest 复验，权威 JSON 为 290032 bytes。
- 持久与部署复验：服务重启后 `status/history/report` 恢复同一 Run；最终在同一真实终端完成 `start.sh → 首页/OpenAPI/health/history/report → stop.sh`，健康为 `ok=true/status=ok`、历史含 2 个 Case、报告深读数为 10，停止后无残留服务。
- 涉及文件：`backend/requirements.txt`、`backend/idea/health.py`、`backend/idea/execution.py`、`backend/tests/test_health.py`、`backend/tests/test_execution.py`、`README.md`、`docs/development-log.md`。
- 测试：健康/API 定向 15 项通过；Execution/Workflow 定向 13 项通过；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q` 共 150 项通过；Skill `quick_validate.py` 通过；`pip check`、`compileall`、配置/Schema JSON、4 个 shell 脚本语法和 `git diff --check` 全部通过。
- 提交主题：`fix(idea): [IDEA-TESTENV-001] restore live test and limitation gates`
- 已知限制：受限沙箱中的 Starlette 同步 `TestClient` 线程/事件循环仍会挂起，项目判据必须使用真实 Linux 环境；在线结论仍受报告中明确的检索、创造性和价值限制约束。模型密钥仅用于临时测试进程，未写入跟踪文件。

## 2026-07-16 — IDEA-BYOK-001

- 类型：前端逐页 BYOK、Run 级临时凭证与持久化入口移除。
- 目标：每次进入或刷新 IDEA 页面都要求用户重新输入自己的 API Token，且 Token 不进入配置文件、数据库、历史、日志、报告或浏览器存储。
- 前端：新增密码型“API Token（本页临时使用）”输入框；`DOMContentLoaded`、`pageshow` 和 `pagehide` 均清空输入值以覆盖普通刷新与 bfcache 返回；创建和重跑均显式携带本页 Token，缺失时在创建 Case/Run 前阻止提交；未使用 localStorage、sessionStorage 或 Cookie。
- 后端：创建 Run 和重跑请求使用 Pydantic `SecretStr` 严格要求非空 Token；Task Manager 只在进程内按 `run_id` 暂存，并通过 `ContextVar` 限定到对应异步执行上下文，完成、失败或取消后立即删除；Run 配置快照、SQLite 和 API 视图均不包含 Token。
- 重启语义：服务不会持久化凭证。重启时遗留的 `QUEUED/RUNNING` Run 保留完整历史并明确进入 `FAILED / RUNTIME_API_KEY_REQUIRED_AFTER_RESTART`，用户重新输入 Token 后才能创建重跑 Run，不再从磁盘认证文件静默恢复。
- 兼容与清理：移除会把明文密钥写入 `config/opencode/opencode.json` 和 `data/opencode/auth.json` 的旧 `/api/config` GET/POST 接口；健康状态改为 `model.runtime_required / per_run`，无服务器级密钥仍可启动并等待页面授权；CLI 只允许从指定环境变量读取 Token，不提供明文命令行参数。
- 文档：同步更新 README、技术设计、Skill 和 Workflow API 恢复契约，明确网页逐页输入、CLI 临时环境变量与服务重启后的失败/重跑行为。
- 测试：新增/调整 API、Task Manager、模型上下文、健康、前端和 Skill CLI 回归；定向 33 项全部通过；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q` 共 154 项通过；`node --check frontend/app.js`、`compileall`、主应用 OpenAPI 必填字段及旧接口缺失检查、`git diff --check` 全部通过。
- 提交主题：`feat(idea): [IDEA-BYOK-001] require ephemeral per-page API keys`
- 已知限制：页面关闭或刷新不会中止已在同一后端进程执行的 Run，因为该 Run 的临时 Token 会保留到终态；后端进程重启后无法继续原 Run，这是“不持久化使用者 Token”的有意安全边界。

## 2026-07-16 — IDEA-UXOBS-001

- 类型：空白工作区重置、完整 BYOK 模型坐标、实时可观测性与结果本地化。
- 目标：修复“＋”只取消 Case 选择但残留表单/结果的问题；让每个 Run 真正使用用户输入的 Base URL、API Key 和 Model；在页面实时显示 Workflow 与 Tool Call；提升专利链接、中文判断和价值评分的可用性。
- 同页重置：点击左侧“＋”后停止当前页面的 SSE/调试轮询但不取消后台 Run，清空 Case、IDEA、三项模型配置、选中 Run、结果、消息、进度与调试面板，日期和检索预算恢复默认值；历史列表继续保留。
- 字段语义：Case 名称新增“同一方案多次 Run 的历史分组、不参与模型判断”说明；“评估日”明确为实际专利公开截止日，“评估日依据”明确为只供报告追溯、不改变计算的来源说明。
- 完整 BYOK：创建/重跑请求严格要求 `base_url + api_key + model`；禁止 Base URL 携带 userinfo、query 或 fragment；三项配置通过 Run 级 `ContextVar` 隔离，模型请求的 URL、Bearer Key 和 payload model 均来自本次页面输入。API Key 只在 Task Manager 内存中保存到终态，Base URL 与 Model 作为非凭证 Run provenance 保存；CLI 同样必须显式提供 Base URL 与 Model，Key 只从指定环境变量读取。
- 运行调试：新增 `RunDebugLog`，逐 Run 追加 Workflow 开始/步骤 attempt/结束、结构化模型调用、检索/全文 Provider 调用、耗时、结果数、usage 和错误摘要到 `workspace/debug/idea-runs/{run_id}.jsonl`；目录已由现有 `workspace/` 规则整体 Git 忽略。日志写入和读取均做敏感字段递归脱敏，不记录 API Key、Authorization、完整 Prompt、完整用户输入或完整模型输出。
- 调试 API/UI：新增 `GET /api/idea/runs/{run_id}/debug`，聚合持久 `run_steps`、`tool_calls` 和 JSONL 事件；页面每秒刷新当前 Workflow、最近事件及 Tool Call 的 Provider、operation、状态、结果数、耗时和错误，Run 终态后停止轮询并保留最后快照。
- 结果体验：深读文献、最接近/破坏性文献、新颖性矩阵及创造性 D1/D2 的公开号均变为安全新标签页链接，优先使用持久 URL、否则构造 Google Patents URL；枚举型判断、申请建议、组合动机和审计等级在 UI 映射为中文；所有 Agent 的面向用户说明统一要求简体中文，确定性审计和常见限制消息同步中文化。
- 价值评分：`ValueDimension.rating` 从 `LOW/MEDIUM/HIGH` 改为严格整数 1–5，提示词写明各档含义及“规避难度高分表示更难绕开”；前端显示 `n/5`。旧报告保持 Manifest 不可变，展示时兼容折算 `LOW=1、MEDIUM=3、HIGH=5`。
- 文档：同步更新 README、技术设计、Skill、Workflow API 与报告字段参考；明确三项逐页输入、调试日志安全边界和 1–5 分制。
- 测试：改动定向 47 项全部通过；新增 JSONL 追加/脱敏/路径约束测试；`PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q` 共 159 项全部通过；`compileall`、`node --check frontend/app.js`、`git diff --check` 通过。使用 `/tmp` 隔离数据库在 `127.0.0.1:8127` 启动真实 Uvicorn，首页三项输入和调试面板、OpenAPI 三项必填、调试路由、`runtime_required` 健康语义均通过，随后正常停机且未调用外部模型。
- 提交主题：`feat(idea): [IDEA-UXOBS-001] add runtime config and live debug UX`
- 已知限制：调试面板展示安全摘要而非完整 Prompt/模型全文，这是防止凭证和大文本泄漏的有意边界；旧三档历史报告只做 UI 兼容折算，不改写原报告或 Manifest。

## 2026-07-16 — IDEA-FETCH-001

- 类型：最新失败 Run 日志诊断、深读标识门禁与并发取消修复。
- 现场证据：Run `4fc955d7-5580-4220-aa4a-fb02b0d0c015` 在 `NORMALIZE_AND_FETCH` 连续三次以 `KeyError('')` 失败；其 `RETRIEVE_CANDIDATES` 检查点的 15 个深读值中包含一个空公开号。失败后同批抓取协程未被 `asyncio.gather` 自动取消，三次 attempt 形成重叠请求，部分 Tool Call 在 Run 已进入 FAILED 后仍继续完成。
- 根因：检索结果允许仅以 URL 建立可追踪身份，但旧深读选择直接用 `publication_number or ""` 生成抓取队列；抓取阶段又以空字符串索引只包含非空公开号的候选字典。并发聚合抛出该异常时没有显式取消和回收兄弟任务。
- 修复：摘要筛选保留 URL-only 候选，但深读选择只接收可标准化公开号并写入中文限制；抓取入口重新标准化、去重并校验旧检查点，空值、重复值和无法回指候选的值均安全跳过并显式留痕；任何内部异常都会取消并等待同批所有任务后再抛出，避免跨 attempt 泄漏。
- 涉及文件：`backend/idea/search_strategy.py`、`backend/idea/retrieval.py`、`backend/tests/test_retrieval.py`、`docs/idea-rebuild-technical-design.md`、`docs/development-log.md`。
- 测试：新增 URL-only 相关候选、旧检查点空值/重复值、内部异常取消兄弟任务三项回归；检索与筛选定向 15 项全部通过；真实 Linux 网络命名空间下完整 162 项全部通过。
- 提交主题：`fix(idea): [IDEA-FETCH-001] harden deep-review fetch queue`
- 历史语义：原失败 Run 和日志保持不可变作为缺陷证据；修复后的重跑会创建新 Run，不改写旧终态。

## 2026-07-16 — IDEA-HISTORY-I18N-001

- 类型：Case/Run 产品语义复审、历史输入恢复与中文输出硬门禁。
- 复审结论：Case 是同一技术方案的历史分组，不参与模型判断；Run 才是一次不可覆盖的完整评估。数据库原本已把每次 Run 的输入保存在独立 `run_inputs` 行并禁止更新，但 Run API 视图遗漏 `input_text/input_hash/date_basis`，前端历史只显示状态、短 ID 和 Markdown 结果，无法观察或复用每次输入差异。Case 标题也允许重名，进一步降低辨识度。
- Case/Run 优化：新 Case 标题去除首尾空格后按英文大小写不敏感保持唯一，冲突返回 409；历史卡增加短 Case ID，Run 增加输入摘要和哈希；点击其他 Case 自动打开最新 Run，点击任意 Run 恢复完整 IDEA、评估日、日期依据和检索预算。历史表单明确标识不可变快照，编辑后提交创建同 Case 新 Run，“重新运行”复制原输入和预算，两者均不覆盖旧记录。
- 中文门禁：结构化模型客户端对文献判断、创造性、价值、审计与报告的全部用户可见字段执行中文占比校验；英文或明显以英文为主的文本触发结构化自动重试，耗尽后步骤失败，不允许英文判断进入新报告。创造性和价值 Prompt 同步强化全部说明字段使用简体中文。
- 历史兼容：既有英文 `report.json/report.md/manifest.json` 保持不可变；前端识别旧英文创造性、价值、审计和限制文本，显示中文说明与明确的重新运行提示，不伪造或改写历史证据。
- 涉及文件：`backend/idea/database.py`、`backend/idea/api.py`、`backend/idea/model_client.py`、`backend/idea/inventiveness.py`、`backend/idea/value_analysis.py`、`frontend/index.html`、`frontend/app.js`、`frontend/style.css`、相关测试、README、技术设计和开发日志。
- 测试：数据库、模型客户端、前端、创造性、价值和审计定向 33 项通过；IDEA API 12 项通过；真实 Linux 环境完整 165 项通过；`node --check frontend/app.js`、`compileall` 和 `git diff --check` 通过。
- 提交主题：`feat(idea): [IDEA-HISTORY-I18N-001] clarify cases and enforce Chinese output`
- 迁移说明：既有重名 Case 不改写、不合并；短 Case ID 保证可辨识。唯一标题规则只约束之后的新建 Case。

## 2026-07-16 — IDEA-PARSE-001

- 类型：最新失败 Run 日志复盘、Unicode source span 确定性修复与空模型响应重试。
- 现场证据：Run `553efc45-9909-4cea-83dd-9139b268e6c5` 仅执行到 `PARSE_IDEA`。attempt 1 和 2 的 DeepSeek 调用均返回并通过结构化 Schema，但分别被后置校验以 `feature span does not match input: F6/F1` 拒绝；attempt 3 收到 HTTP 200 的空 assistant content，最终终态只显示 `ModelClientError: model assistant content is empty`，掩盖了前两个主要失败。
- 根因：旧 Prompt 要求模型精确计算中文 Unicode 零基字符 offset，且后端把 offset 作为权威值直接切片校验；模型引用文字可能正确，但偏移计数不可靠。空 assistant content 又没有进入结构化重试分支，直接消耗完整 Workflow attempt。
- 修复：`explicit` 特征的逐字引用文本改为权威依据，后端查找所有精确出现位置并选择最接近模型建议 offset 的位置，确定性回填 start/end；原文不存在或引用为空仍严格失败，不做模糊匹配。同一 Agent 调用中的空 assistant content 现在作为结构化验证错误自动重试，耗尽后才上升到 Workflow。
- 涉及文件：`backend/idea/agents.py`、`backend/idea/model_client.py`、`backend/tests/test_agents.py`、`backend/tests/test_model_client.py`、技术设计、Skill 输入参考和开发日志。
- 测试：新增错误中文偏移修正、重复引用就近消歧、空 assistant content 内部重试三项回归；Parser/模型/执行器定向 20 项通过；真实 Linux 环境完整 168 项通过；`compileall` 和 `git diff --check` 通过。
- 提交主题：`fix(idea): [IDEA-PARSE-001] resolve source spans deterministically`
- 历史语义：原失败 Run 和完整 attempt/tool-call 日志保持不变；重跑会创建新 Run，并使用修复后的解析规则。

## 2026-07-16 — IDEA-STABILITY-001

- 类型：多 Run 并发真实压测、文献分析竞态修复、证据引用稳定化与深读不足降级。
- 现场证据：Run `912de090-167e-458e-8b9d-d73011541835` 首次文献分析因模型公开号回显不一致失败；未取消的兄弟协程继续写库，重试随后读取到被另一并发 Run 清空的共享全文并以 `fetched document text is unavailable` 终止。Run `df6f1c96-5efa-4b71-92c8-a71aad2b7cdb` 同样因跨 attempt 后台写入而出现 `document analysis already exists for this run`。
- 并发与共享状态修复：文献分析改为显式创建任务，任一异常时取消并等待所有兄弟任务；公开号按不可变输入元数据确定性恢复；共享 `patent_documents` 只有在没有其他待深读引用时才释放可重建全文，避免 Run 间互相破坏。
- 证据引用修复：真实并发 Run `51b58bdc-a87c-458e-9a97-f1dd1b5c5ba6` 和 `da200454-e5cb-4b6f-83bb-32008c365461` 均完成 11/11 步，但文献 Agent 曾把长哈希 evidence ID 简写为 `E-1/E-5`，触发整批步骤重跑。模型输入现使用文档内短别名 `E1..En`，后端无歧义映射回真实哈希，未知编号仍严格拒绝。
- 深读不足修复：随机 Run `a1f9ae86-8da2-4c92-8ac7-3fe29f8b800e` 只有 4 篇相关全文，旧硬门槛连续三次重复抓取后失败。流程现仅在零篇全文时失败；1–9 篇时继续审计并强制降低结论强度。相同输入重跑 `e4c638ab-c67e-4554-91e8-24befc0c8418` 完成 11/11 步、所有 Workflow 步骤均 attempt 1，以 2 篇深读得到 `UNCERTAIN / COMPLETED_WITH_LIMITATIONS`，没有伪装成肯定新颖性。
- 报告与启动修复：Markdown 公开号改为安全链接，申请建议、创造性状态和审计等级使用中文，三个价值维度显示 `n/5`；深读不足限制补充中文说明；审计 Prompt 区分价值 basis ID 与专利 evidence ID；`start.sh` 本机 OpenAPI 探活强制绕过环境代理，避免服务已启动却误报失败。
- 涉及文件：`backend/idea/document_analysis.py`、`execution.py`、`retrieval.py`、`audit.py`、`reporting.py`、`start.sh`、相关测试、技术设计和开发日志。
- 测试：三组随机真实 DeepSeek/双 Provider Run，其中两组并发；前两组均 `COMPLETED_WITH_LIMITATIONS`，分别 33/37 次 Tool Call，第三组首次暴露深读硬门槛后按不可变历史保留；同输入修复后重跑 15 次 Tool Call、11/11 步一次通过。新增公开号回显、证据别名、兄弟任务取消、共享全文保留、深读降级、中文报告与限制消息回归；完整后端测试、`compileall`、Shell 语法和 `git diff --check` 通过。
- 历史语义与凭证：所有失败和成功 Run 均保留，不修改历史报告；API Key 只进入获授权的测试进程，未写入配置、数据库、日志、文档或提交内容。

## 2026-07-16 — IDEA-REPORT-001

- 类型：最终报告新颖性一致性校验误判修复。
- 现场证据：Run `e51e3410-5093-4769-a0b2-bdec0bdd98f9` 已完成 15 篇深读，确定性新颖性结果为 `NOT_NOVEL`、置信度 1.0，单篇破坏性文献为 `CN111597801A`，审计无问题；三个报告 Composer 调用均成功，但 `AUDIT_AND_REPORT` 三次被 `report narrative contains a conflicting novelty conclusion` 拒绝，未写入报告。
- 根因：旧校验使用普通子串搜索；正确短语“不具备新颖性”包含“具备新颖性”，报告后文重复正确否定结论时被误判为相反结论。
- 修复：三种中文结论改用有边界的完整短语匹配，否定短语优先，肯定短语使用负向后行断言保护；同一正确结论可重复出现，真正混入其他两种结论仍严格失败。
- 测试：新增三种结论重复出现均通过、否定结论后混入肯定结论仍拒绝的回归；旧的“前缀正确但后文矛盾”测试继续保留。
- 历史语义：失败 Run 保持不可变；修复后需要通过“重新运行”创建新 Run 才会生成报告。

## 2026-07-17 — IDEA-ARK-001

- 类型：火山方舟 Coding Plan 推理模型结构化输出兼容性。
- 现场证据：使用 Run 级临时配置调用火山方舟 OpenAI 兼容网关和 `kimi-k2.6` 时，两组并发 Run 均在 `PARSE_IDEA` 首次调用 180 秒后以 `ReadTimeout` 失败并进入 Workflow 重试；相同密钥、Base URL 和模型的最小直连请求可正常返回，排除凭证、模型名和基础连通性问题。
- 根因：该模型默认返回并计费 `reasoning_content`。简单请求在默认模式下也可能把输出额度消耗在推理过程并留下空 `content`；同一请求使用火山方舟支持的 `thinking.type=disabled` 后约一秒返回正常正文。IDEA Agent 需要严格 JSON 提取而非长链推理，默认深度思考会放大延迟、空正文和超时风险。
- 修复：结构化模型客户端仅在运行时 Base URL 的主机属于 `volces.com` 时注入 `thinking: {"type": "disabled"}`；其他 OpenAI 兼容 Provider 的请求格式保持不变。适配依据主机而非模型名称，兼容同一网关的模型升级，同时避免把厂商扩展字段发送给 DeepSeek 等服务。
- 真实回归：修复后两条并发 Run 和一条加载 Google 检索修复后的独立 Run 均完成报告，分别产生 15、18、18 次结构化模型调用；最长单次调用约 141 秒，三条 Run 均无 `ReadTimeout` 或 Tool Call 错误。
- 安全：API Key 只存在于获授权的临时测试 shell 和对应 Run 的后端内存中；日志、数据库、文档和代码不包含凭证。

## 2026-07-17 — IDEA-GPAT-001

- 类型：Google Patents 本地检索 Provider 线上兼容性修复。
- 现场证据：Google Patents 搜索 URL 仍返回 HTTP 200，但响应已变为约 4 KB 的动态前端壳页面，不再包含 `search-result-item`；旧解析器因而把每次调用记录为 `EMPTY`，使配置中的双 Provider 检索实际退化为仅依赖 EXA。详情页仍返回可解析的专利正文。
- 根因：Provider 继续从搜索页面 HTML 提取结果，没有跟随当前前端使用的 `/xhr/query` JSON 数据接口。
- 修复：搜索请求改用同源 `/xhr/query`，解析 `results.cluster[].result[].patent`，清理标题与摘要中的高亮 HTML 和实体，并保留公开号、日期、受让人及可追踪详情 URL；异常响应不再静默伪装为空结果。专利详情抓取路径不变。
- 回归：新增当前 JSON 结构、HTML/实体清理、嵌套查询参数、结果上限、缓存及网络错误测试；真实网络探针成功返回可追踪公开号与标题。修复后的全工作流回归中，7 次 Google 搜索有 4 次命中，共返回 30 条结果；其余 3 次确为严格组合查询无命中。

## 2026-07-17 — IDEA-AUDIT-002

- 类型：证据语义审计清单回显的稳定性修复。
- 现场证据：真实回归已完成全部检索、正文分析、新颖性、创造性与价值步骤，但语义审计 Agent 首次返回的 `checked_publication_numbers` 少于确定性冻结清单，触发 `AgentExecutionError`，Workflow 只能重跑整个 `AUDIT_AND_REPORT`；第二次审计返回完整清单后成功继续。
- 设计约束：不取消“必须精确回显全部证据 ID 与公开号”的审计门槛；它用于证明语义审计覆盖的是当前 Run 的完整冻结证据集。
- 修复：审计服务发现清单差异后，在服务内部追加一次带明确错误原因和权威完整清单的纠错调用；只有纠错后仍不一致才交给 Workflow 重试。报告生成、确定性审计和未知证据 ID 拒绝规则不变。
- 回归：新增“首次漏回公开号、第二次按纠错清单成功”的测试；持续保留“连续漏回证据 ID 必须失败且不得持久化审计结果”的严格测试。
- 总体验证：目标回归 20 项、真实 Linux 完整测试 179 项、`compileall`、`pip check`、Manifest 哈希校验和 `git diff --check` 均通过；三份真实报告都使用中文判断、1–5 分评分和可点击专利链接。

## 2026-07-20 — AIF-BOOT-001

- 类型：AIFPatent 独立项目初始化。
- 来源：从 AI4Patent 当前工作树复制源码，包含尚未提交但已验证的方舟推理关闭、Google Patents JSON 搜索和审计清单纠错修复。
- 隔离：未复制原 `.git`、`config/opencode`、虚拟环境、下载引擎、运行数据库、Case/Run、工作区缓存、日志或认证文件。
- 新项目：目录为 `/home/bok/code/patent_prototype/AIFPatent`，默认分支为 `main`，远程目标为 `BeingOK3/AIFPatent`。
- 迁移原则：LangGraph 替换执行编排层，LangChain 提供模型适配；专利领域逻辑、业务 SQLite、Evidence、审计与 Manifest 保持权威。

## 2026-07-20 — AIF-OC-001

- 类型：新项目 OpenCode 运行依赖清理。
- 删除：`backend/opencode_client.py`、临时 `backend/vk.py`、`/api/run`、`/api/stop`、OpenCode 健康探针、二进制下载安装分支和认证文件回退。
- 保留：IDEA Case/Run API、SSE、持久步骤、Provider、审计、报告与文件接口；原薄客户端迁为 `tools/idea_workflow.py`，仅通过 HTTP 调用权威 API。
- 凭证：网页 Run 仍使用临时 Base URL、API Key、Model；CLI 只从显式环境变量读取 Token。服务器不再读取任何认证 JSON 文件。
- 存储：业务 SQLite 改用 `data/aifpatent/`，为后续 LangGraph 检查点预留 `data/langgraph/`，二者均 Git 忽略。
- 验证：后端、配置、脚本和 CLI 中无运行时 OpenCode 引用；借用原项目既有 Python 环境执行新项目完整 179 项离线测试，全部通过。

## 2026-07-20 — AIF-GRAPH-001

- 类型：LangGraph 编排与 LangChain 模型层核心迁移。
- 图拓扑：新增 `IdeaGraphState` 和固定 11 节点 `StateGraph`；节点名称继续使用原权威步骤名，边固定为线性顺序，模型不得选择、跳过或循环业务步骤。
- 持久化：`run_id` 作为 LangGraph `thread_id`，`data/langgraph/checkpoints.db` 仅保存 `run_id`、最后完成节点和完成计数；专利正文、Evidence、结论、审计、报告继续以业务 SQLite 与 RunStore 为权威。
- 可靠性：LangGraph RetryPolicy 负责节点级重试，现有 Harness 记录每个业务 attempt、写一次结果和完成门禁；单节点超时、进程取消和服务关闭均有确定性终态与 JSONL 事件。
- 模型层：默认传输改为 LangChain `ChatOpenAI`；每次 Run 的 Base URL、API Key、Model 仍由 ContextVar 临时提供。DeepSeek/OpenAI-compatible JSON mode、火山方舟 `thinking.disabled`、代理失败直连回退、严格 Pydantic Schema 和中文门禁保持不变。
- 安全：新增回归在临时 Runtime Context 放入哨兵密钥并执行完整图，确认 LangGraph SQLite 不包含该值；API Key 也不进入 Graph State、业务配置快照、日志或前端存储。
- 可观测性：前端调试时间线识别 LangGraph 图开始、节点开始/完成/失败/取消和图终态；系统健康检查新增 `langgraph_checkpointer` 组件。
- 验证：完整 181 项离线测试通过；`compileall`、`node --check`、四个 Shell 脚本语法、`pip check` 和 `git diff --check` 通过。

## 2026-07-20 — AIF-STABILITY-001

- 类型：真实模型全链路回归、创造性证据绑定修复与图并发/取消补强。
- 图级回归：新增两个 Run 同时通过一个 CompiledStateGraph 执行的测试，SQLite Checkpointer 记录两个独立 `thread_id`；新增运行中节点取消测试，确认 asyncio Task、LangGraph 节点、业务 attempt 和 Run 依次进入 `INTERRUPTED/CANCELLED`。
- 真实模型冒烟：使用获授权的临时火山方舟 Base URL、Token 和 `kimi-k2.6` 通过 LangChain 完成 IDEA Parser，第一次返回合法结构，约 15.8 秒、1047 tokens；Token 未写入仓库或运行文件。
- 首次真实 quick Run：随机工业边缘缓存方案完成前 8 个节点后，`ANALYZE_INVENTIVENESS` 三次失败，错误均为 `every cited D2 publication requires bound evidence`。现场表明所有 D2 候选都带合法证据，但模型列出部分 D2 公开号时遗漏该文献自己的 evidence ID；原 Prompt 没有明确一对一绑定要求，后置校验也没有把精确错误反馈给模型。
- 修复：保留严格门禁，明确“每个 D2 公开号至少绑定一条同候选证据”；首次领域校验失败后，在同一 LangGraph 节点内附上按 feature/publication 分组的合法 evidence ID 清单进行一次定向纠错。第二次仍无效则继续失败，不删除、不猜测、不跨文献补证据。
- 修复后真实 quick Run：同一 Case 下创建不可变新 Run，完成 `COMPLETED_WITH_LIMITATIONS`，11/11 节点全部 attempt 1，总耗时约 445.7 秒；40 个候选、10 篇深读、41 次 Tool Call，结论“具备新颖性”，价值评分为 3/5、4/5、4/5。
- 报告门禁：中文新颖性/创造性/价值说明、Google Patents 可点击链接、`report.json`、`report.md` 和 Manifest 哈希全部通过；临时 API Key 在 API 返回、业务 SQLite、LangGraph SQLite、workspace 和 logs 中均无命中。
- Provider 降级：首次 Run 的 Google Patents 搜索与全文抓取成功；短时间内重跑时 Google `/xhr/query` 返回 HTTP 503，配置的三次 Provider 重试耗尽后由 EXA 16 次成功调用支撑完整报告，终态按设计标为 `COMPLETED_WITH_LIMITATIONS`，未伪装成全 Provider 正常。
- 清理：真实测试 Case 含失败 Run 和修复后成功 Run，共 2 条，在验证完成后通过官方删除 API 清理；脱敏 JSONL 仍按 Git 忽略策略留在本机用于本次调试证据。
- 品牌与传输：首页、Schema、Provider User-Agent/MCP Client 名称统一为 AIFPatent；LangChain 自定义 HTTP 客户端显式关闭额外 socket option 注入，避免代理行为警告。

## 2026-07-20 — AIF-DELIVERY-001

- 独立仓库：`BeingOK3/AIFPatent`，`main` 保存孵化基线，`feat/langgraph-migration` 保存完整迁移提交链。
- 提交链：`ae2331c` 独立基线、`0a32493` 移除 OpenCode、`20587d9` LangGraph/LangChain 核心迁移、`5e44e1f` 真实 E2E 稳定性修复。
- 最终离线验证：184 项测试通过；Python、JavaScript、Shell、依赖一致性、diff 格式和敏感凭证模式扫描全部通过。
- 原项目隔离：迁移前后 AI4Patent HEAD 均为 `8a2795819cbed647c36e5d2cde49eb6730627228`，工作树状态哈希均为 `5bdb5399664ed99835633e56cab71e8066684248b9787787d104f2b7f804be04`，源码内容哈希均为 `9ee3381d78e25706142054e4b9ffc87cbfceb366d8f2f5a8ab51b04780f6bb4e`。

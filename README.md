# AIFPatent — LangGraph 专利 IDEA 工作台

AIFPatent 是从 AI4Patent 当前工作树独立孵化的新项目。它拥有全新的 Git 历史和远程仓库，目标是在保留专利证据、审计、不可变 Run 与 BYOK 安全语义的前提下，将执行编排迁移到 LangGraph，并使用 LangChain 统一模型结构化调用。

当前仓库不继承原项目的 `.git`、运行数据、缓存、日志或凭证。迁移过程和每个里程碑的验证证据记录在 `docs/development-log.md` 与 `docs/commit-ledger.md`。

AIFPatent 当前只对外启用“专利 IDEA 评估”。系统把检索、全文核验、新颖性、创造性、价值分析、审计和报告固化为后端 Workflow；CLI 只负责提交任务、等待持久终态和读取权威报告，不能自行跳步或模拟工具结果。

## 当前能力

- 固定 11 步 Workflow，每一步都有 attempt、状态、错误码和 write-once 检查点。
- 本地 Google Patents 与 EXA MCP 并行检索，结果独立留痕、归一化、合并去重；单路故障可降级。
- 先基于标题/摘要和中英双语概念组筛选，再对相关候选读取全文；深读下限固定为 10 篇。
- 新颖性遵守单篇文献原则，可直接输出“具备新颖性”，同时给出置信度、最接近文献、缺失特征、检索范围和局限。
- 创造性、价值、模拟审查意见和证据审计均使用严格 JSON Schema；模型不能伪造 evidence ID。
- 价值维度使用 1–5 分制；面向用户的判断文字统一为中文，报告中的专利公开号可直接打开原文。
- Case/Run、输入快照、报告和 Manifest 持久保存；历史不自动删除，只支持用户手动删除。
- 页面实时显示 Workflow 步骤和 Tool Call；详细事件追加到 Git 忽略的 JSONL 调试日志。
- 可重建缓存使用 1 GiB 上限和 FIFO 清理，不会清理 Case/Run 权威结果。

## 快速开始

环境要求：Linux、Python 3.10+、`bash`、`curl`，以及可访问模型 API 和至少一个专利检索 Provider 的网络。

```bash
git clone git@github.com:BeingOK3/AIFPatent.git
cd AIFPatent
./install.sh
```

存储、缓存、检索 Provider 和预算统一配置在 `config/ai4patent.json`。每个网页 Run 的模型 Base URL、API Key 和 Model 由使用者临时输入；API Key 不写入配置文件、数据库、历史、日志或浏览器存储。

启动服务：

```bash
./start.sh
# 浏览器访问 http://localhost:8001
```

打开页面后，在“模型 API（本页临时使用）”中输入自己的 Base URL、API Key 和 Model。三项内容在刷新、关闭、重新进入页面或点击左侧“＋”后都会清空；API Key 只在当前页面和对应 Run 的进程内存中使用。

开发模式与停止：

```bash
./dev.sh
./stop.sh
```

## 使用方式

网页为三栏 IDEA 工作区：

1. 左侧查看共享 Case/Run 历史和终态。Case 是同一技术方案的历史分组，名称必须唯一；每个 Run 都是带独立输入哈希的不可变快照，不会覆盖其他 Run。
2. 中间输入本页临时 Base URL、API Key、Model 和技术方案，选择评估日、quick/standard/deep、候选上限和深读上下限。
3. 运行中查看 11 步持久进度，以及实时 Workflow、Tool Call、耗时、结果数和错误；刷新或断线后可从持久状态恢复显示。
4. 右侧查看中文新颖性、创造性、1–5 分价值、审计、Provider 状态和限制；专利号可打开原文，并可导出 Markdown。

点击 Case 会打开其最新 Run，点击任意 Run 会把当次 IDEA、评估日、日期依据和检索预算恢复到中栏。编辑这些历史输入后提交会在当前 Case 下创建新 Run；“重新运行”会复制原 Run 的输入和预算。两种方式都只新增记录，不修改旧 Run。修复前已经生成的英文历史报告保持 Manifest 不变，页面会显示中文兼容说明；新 Run 的创造性、价值、审计、限制和报告说明必须通过中文语言门禁，否则模型调用会自动重试。

默认检索预算：

| 模式 | 候选上限 | 深读下限 | 深读上限 |
|---|---:|---:|---:|
| quick | 30 | 10 | 10 |
| standard | 80 | 10 | 20 |
| deep | 150 | 20 | 40 |

系统会根据 IDEA 的宽窄在上下限之间确定目标。用户可以修改上限，但深读下限不能低于 10，候选上限不能小于深读上限。

## CLI

服务启动后可直接运行确定性 CLI：

CLI 不接受明文 `--api-key` 参数；启动 Run 时只从当前进程的环境变量读取 Token：

```bash
export LLM_API_KEY='your-key'
tools/idea_workflow.py health

tools/idea_workflow.py run \
  --model-base-url https://api.deepseek.com \
  --model deepseek-v4-flash \
  --idea '一种具体的技术方案……' \
  --evaluation-date 2026-07-16 \
  --mode quick \
  --candidate-max 30 \
  --deep-min 10 \
  --deep-max 10
```

CLI 只有在 Run 到达成功终态后才返回报告；失败、取消、健康门禁失败或输入非法均返回非零退出码。

服务重启不会恢复任何 Token。重启时尚未结束的 Run 会进入 `FAILED / RUNTIME_API_KEY_REQUIRED_AFTER_RESTART`；在网页重新输入 Token 后使用“重新运行”创建新 Run，历史记录仍保留。

## 运行状态与故障语义

- `COMPLETED`：11 步、审计和 Manifest 全部通过。
- `COMPLETED_WITH_LIMITATIONS`：报告有效，但存在明确限制，例如一路 Provider 降级。
- `FAILED`：步骤重试耗尽或完成门禁失败，不会生成伪成功报告。
- `CANCELLED`：用户取消，保留已产生的审计记录。

本地 Google Patents 不需要单独服务，但仍依赖当前主机访问 `patents.google.com`；“本地”指工具由本项目实现，并不等于离线镜像。EXA MCP 是独立备路。两路都不可用时检索会失败，模型记忆不能替代真实检索。FIFO 缓存可复用已经成功抓取的数据，但不是完整专利数据库。

健康检查：

```bash
curl http://127.0.0.1:8001/api/system/health
curl http://127.0.0.1:8001/api/system/cache
# 将 RUN_ID 替换为实际值，查看持久步骤、Tool Call 和 JSONL 事件
curl http://127.0.0.1:8001/api/idea/runs/RUN_ID/debug
```

## 测试

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q
python3 -m compileall -q backend tools
```

测试使用 fixture 或 fake transport 时不依赖外网；真实 E2E 需要模型密钥和检索网络。

## 主要目录

```text
backend/idea/              Workflow、Provider、Agent Schema、审计和报告
backend/tests/             离线单元/合约/集成测试
frontend/                  IDEA 单页工作区
config/ai4patent.json      唯一系统设置入口（不含密钥）
tools/idea_workflow.py     确定性 HTTP CLI（不含模型编排）
data/aifpatent/            SQLite 业务数据（Git 忽略）
data/langgraph/            LangGraph 检查点（Git 忽略）
workspace/idea-runs/       不可自动删除的 Run 输入与报告（Git 忽略）
workspace/debug/idea-runs/  逐 Run JSONL 调试日志（Git 忽略，不记录 API Key）
workspace/cache/           1 GiB FIFO 可重建缓存（Git 忽略）
docs/                      技术设计与只追加开发日志
```

当前 LangGraph/LangChain 架构见 `docs/aifpatent-architecture.md`，迁移计划见 `docs/migration-plan.md`，开发与测试证据见 `docs/development-log.md`。`docs/idea-rebuild-technical-design.md` 作为迁移前领域设计背景保留。

## License

MIT

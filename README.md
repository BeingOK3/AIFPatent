# AIFPatent — 可验证 Citation 的专利 IDEA 评估

AIFPatent 当前 `develop` 版本提供可直接运行的首次报告 `LEXICAL_RAG` 闭环：检索专利、深读全文、保存不可变 Corpus Version 与结构化 Chunk，按 `Feature × Patent` 检索证据，强制覆盖摘要和全部独立权利要求，最终生成带可回查 Citation 的 JSON/Markdown 报告。

## 当前版本

已完成：

- 固定 11 步 LangGraph Workflow、不可变 Case/Run、重试、取消、审计和 Manifest；
- Google Patents 与 EXA MCP 检索、全文抓取、候选去重和最少 10 篇深读门禁；
- PostgreSQL Corpus Version/Chunk、MinIO 正文对象、Redis 基础设施；
- PostgreSQL FTS/`pg_trgm` 词法检索，严格限制在源 Run 的冻结 Version；
- 每个 `F_i × D_j` 检索审计，强制摘要、全部独立权利要求和父权利要求链；
- 在文档分析前生成的确定性 Context Manifest，DeepSeek 只使用其中的 `C#` 证据；
- 报告 schema 2.0 Citation 只包含模型对 `DISCLOSED/PARTIAL` 实际输出的 `C#`，并在输出前回查当前 Run 的 READY Version 与真实 Chunk；
- `./start.sh` 和 `./stop.sh` 管理完整 Docker 栈，停止不删除数据卷。

当前 `develop` 已默认启用报告内证据追问 MVP：可从完成的首次报告选择深读文献建立 Thread，每轮重新检索冻结 Corpus，运行独立七节点 Workflow，输出结构化回答并展开可回查的 Citation 原文；页面提供 SSE 状态与取消，模型 Base URL/Model/API Key 仍为刷新即丢失的瞬时 BYOK。首次报告默认仍使用已验收的 `LEXICAL_RAG`；追问在未配置部署级 Embedding 时明确降级为 `LEXICAL_ONLY`，不会伪装成向量混合召回。

尚未完成：真实 DeepSeek 追问运行态验收、首次报告默认 Hybrid 切换、reranker、专利族变体与法律状态增强、多租户/认证。详细边界记录在 `development/followup-rag/development-log.md`。

## 新机器直接运行

需要 Linux、Git、Docker Engine、Docker Compose plugin、Python 3 和 curl。先按 Docker 官方方式安装 Engine/Compose，并让当前用户可执行 Docker；重新登录后确认：

```bash
docker version
docker compose version
```

克隆并启动：

```bash
git clone --branch develop git@github.com:BeingOK3/AIFPatent.git
cd AIFPatent
./start.sh
```

首次启动会检查至少 5GiB 可用空间，创建 Git 忽略且权限为 `0600` 的 `deploy/rag/rag.env`，启动 app/PostgreSQL/Redis/MinIO，执行幂等迁移、创建 Corpus Bucket 并等待健康检查。`rag.env` 只有本机基础设施随机凭证，不含模型密钥。

浏览器访问 `http://localhost:8001`。停止服务并保留数据：

```bash
./stop.sh
```

如果通过 VS Code Remote SSH 使用服务器，并已配置：

```sshconfig
LocalForward 8001 127.0.0.1:8001
```

则在 Mac 浏览器打开 `http://localhost:8001` 即可访问服务器应用，不需要开放公网端口。

## 模型 BYOK

Base URL、Model 和 API Key 必须在网页“模型 API（本页临时使用）”中按 Run 输入。它们不会写入 `rag.env`。API Key 只进入该 Run 的进程内存，刷新、关闭页面、新建工作区或服务重启后消失；未完成 Run 在服务重启后会明确失败，要求重新输入凭证并重跑。

首次报告完成后，结果区会出现“基于本报告继续追问”。选择允许检索的深读文献并创建会话后，每次发送问题仍使用页面顶部三项模型配置；服务重启时未完成 Turn 会失败并要求重新提交，不会保存或恢复 API Key。

模型 ID 按供应商规则区分大小写。DeepSeek 当前接口返回的是 `deepseek-v4-flash`/`deepseek-v4-pro` 这类规范小写 ID；页面应填写接口实际返回的 ID，而不是展示标题。

CLI 也只从当前进程环境读取凭证，不接受明文 `--api-key`：

```bash
read -s LLM_API_KEY
export LLM_API_KEY
tools/idea_workflow.py run \
  --model-base-url https://api.deepseek.com \
  --model YOUR_MODEL_NAME \
  --idea '一种根据访问热度调整缓存淘汰优先级的方法……' \
  --mode quick --candidate-max 30 --deep-min 10 --deep-max 10
unset LLM_API_KEY
```

## 运行与故障语义

- `COMPLETED`：工作流、审计、Citation 和 Manifest 全部通过；
- `COMPLETED_WITH_LIMITATIONS`：报告有效，但 Provider 降级或来源缺字段等限制已明确记录；
- `FAILED`：步骤重试耗尽、证据/Citation 不一致或完成门禁失败，不生成伪成功报告；
- `CANCELLED`：用户取消，已生成的审计记录保留。

健康和调试：

```bash
curl http://127.0.0.1:8001/api/system/health
curl http://127.0.0.1:8001/api/system/cache
curl http://127.0.0.1:8001/api/idea/runs/RUN_ID/debug
```

## 开发与测试

纯 SQLite 兼容开发模式仍可使用：

```bash
./install.sh
./start.sh --local
./stop.sh --local
```

完整离线测试：

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q
python3 -m compileall -q backend tools
```

真实 LEXICAL_RAG 验收工具只读取临时环境变量并只打印计数摘要；它会独立回查 PostgreSQL 中的 Context、冻结 Version、Chunk 与模型 Citation 选择：

```bash
tools/e2e_lexical_rag.py \
  --model-base-url https://api.deepseek.com \
  --model YOUR_MODEL_NAME \
  --idea '一种具体、完整、可检索的技术方案……'
```

主要目录：

```text
backend/idea/                 Workflow、Corpus、检索、Context、Citation、报告
backend/tests/                单元、合约与可选真实集成测试
frontend/                     IDEA 单页工作区（BYOK 不持久化）
config/ai4patent.json         系统设置（不含模型密钥）
deploy/rag/                   Docker Compose 与幂等 PostgreSQL 迁移
tools/idea_workflow.py        确定性 HTTP CLI
tools/e2e_lexical_rag.py      真实首次报告验收与 Citation 校验
development/followup-rag/     本开发域设计、日志和未完成项
```

## License

MIT

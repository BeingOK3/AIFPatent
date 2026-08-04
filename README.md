# AIFPatent — 专利 IDEA 评审、证据追问与专利态势分析

AIFPatent 当前 `develop` 版本提供可直接运行的首次报告 RAG 闭环：检索专利、深读全文、保存不可变 Corpus Version 与结构化 Chunk，通过首次报告和追问共用的 `HybridRetriever` 按 `Feature × Patent` 检索证据，强制覆盖摘要和全部独立权利要求，最终生成带可回查 Citation 的 JSON/Markdown 报告。默认部署尚未配置 Embedding，因此当前实际召回明确标记为 `LEXICAL_ONLY`。

## 当前版本

已完成：

- 固定 11 步 LangGraph Workflow、不可变 Case/Run、重试、取消、审计和 Manifest；
- 默认通过 SerpAPI Google Patents 检索和抓取全文，保留显式启用的 Google Patents 直连与 Exa MCP 回退，并执行候选去重和最少 10 篇深读门禁；
- PostgreSQL 统一业务数据库（IDEA、Landscape、Corpus、追问和工作流）、MinIO 正文对象；
- PostgreSQL FTS/`pg_trgm` 词法检索，严格限制在源 Run 的冻结 Version；
- 每个 `F_i × D_j` 检索审计，强制摘要、全部独立权利要求和父权利要求链；
- 在文档分析前生成的确定性 Context Manifest，DeepSeek 只使用其中的 `C#` 证据；
- 报告 schema 2.0 Citation 只包含模型对 `DISCLOSED/PARTIAL` 实际输出的 `C#`，并在输出前回查当前 Run 的 READY Version 与真实 Chunk；
- 独立专利态势分析页面和 8 步 Workflow，支持技术方向、重点友商及组合模式，输出唯一合格专利的公司分布、法域、技术聚类、逐件精读与全族状态；
- `./start.sh` 和 `./stop.sh` 管理完整 Docker 栈，停止不删除数据卷。

当前 `develop` 已默认启用并通过真实 DeepSeek 运行态验收的报告内证据追问 MVP：可从完成的首次报告选择深读文献建立 Thread，每轮重新检索冻结 Corpus，运行独立七节点 Workflow，输出结构化回答并展开可回查的 Citation 原文；页面提供 SSE 状态与取消，模型 Base URL/Model/API Key 仍为刷新即丢失的瞬时 BYOK。首次报告与追问现在共用相同的 RRF、章节权重、多样性和冻结范围门禁；未配置部署级 Embedding 时均明确降级为 `LEXICAL_ONLY`，不会伪装成向量混合召回。

尚未完成：选择并配置真实跨语言 Embedding Provider 后的质量验收、真实人工标注评测集、reranker、专利族变体与法律状态增强、多租户/认证。详细边界记录在 `development/followup-rag/README.md`。

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

首次启动会检查至少 5GiB 可用空间，创建 Git 忽略且权限为 `0600` 的 `deploy/rag/rag.env` 和 `config/provider-credentials.local.json`，先启动 PostgreSQL/MinIO 并执行幂等迁移，再启动 app、创建 Corpus Bucket 并等待健康检查。`rag.env` 只有本机基础设施随机凭证，不含模型密钥。

SerpAPI Key 只写入本地私密 JSON；GitHub 只保留模板：

```bash
cp -n config/provider-credentials.example.json config/provider-credentials.local.json
chmod 600 config/provider-credentials.local.json
# 编辑 provider-credentials.local.json，把模板值替换成真实 SerpAPI Key
./start.sh
```

真实文件已同时加入 `.gitignore` 和 `.dockerignore`；Compose 将其作为只读 Secret 挂载，不会烘焙进应用镜像。

IDEA 与专利态势分析当前共享同一个 SerpAPI 主 Provider、本地 Secret 和请求缓存。匿名 Exa MCP 在无独立额度时容易返回 429，Google Patents 直连在部分国内网络会连接超时，因此两者默认关闭但实现仍保留；部署环境具备 Exa 额度或可访问 Google 时，可在 `config/ai4patent.json` 对应 Provider 中重新设置 `enabled: true`。不要用 `ping` 判断搜索是否可用：不少站点会禁用 ICMP，应以 `/api/system/health` 和实际 HTTPS 请求为准。

浏览器访问 `http://localhost:8001` 使用 IDEA 评审；从首页进入专利态势分析，或直接访问 `http://localhost:8001/landscape`。停止服务并保留数据：

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

聊天模型 BYOK 与 Embedding 凭证是两个独立安全域。默认 `AIFPATENT_EMBEDDING_ENABLED=false`，不会请求 Embedding 服务。需要启用真正的向量混合召回时，只在服务器 Git 忽略且权限为 `0600` 的 `deploy/rag/rag.env` 中加入部署级参数，不修改 `config/ai4patent.json`：

```dotenv
AIFPATENT_EMBEDDING_ENABLED=true
AIFPATENT_EMBEDDING_PROVIDER=openai-compatible
AIFPATENT_EMBEDDING_MODEL=YOUR_MULTILINGUAL_EMBEDDING_MODEL
AIFPATENT_EMBEDDING_BASE_URL=https://YOUR_EMBEDDING_PROVIDER/v1
AIFPATENT_EMBEDDING_DIMENSIONS=1024
EMBEDDING_API_KEY=YOUR_DEPLOYMENT_EMBEDDING_KEY
```

首次启用或更换模型/维度后，先回填已有 READY Chunk，再启动应用：

```bash
docker compose --env-file deploy/rag/rag.env --file deploy/rag/compose.yml run \
  --rm --no-deps --volume "$PWD:/workspace:ro" --workdir /workspace \
  --env PYTHONPATH=/workspace/backend app python tools/index_embeddings.py
./start.sh
```

在已安装后端依赖的源码开发环境也可直接运行 `python3 tools/index_embeddings.py`。不要把网页输入的 DeepSeek 聊天 Key 当作 Embedding 凭证；两者不会互相回退。

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

若旧版本 Run 报 `mandatory independent-claim evidence is missing for cv-...`，先更新 `develop` 并执行 `./start.sh` 重建应用，然后在页面新建 Run。失败 Run 的 Corpus Version 与抓取 checkpoint 按审计要求保持冻结，原 Run 的“重试”不会改写旧证据；新 Run 会在全文阶段拒绝缺少摘要/独立权利要求的详情响应，尝试其他 Provider，并从同轮合格候选中补位。新版兜底错误会同时给出公开号、claims Chunk 数和 Chunker 版本，便于通过上述 debug 接口定位。

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
backend/landscape/            专利态势独立 Workflow、检索、精读、聚类与报告
config/ai4patent.json         系统设置（不含模型或检索密钥）
config/provider-credentials.example.json  检索凭证模板（可提交）
deploy/rag/                   Docker Compose 与幂等 PostgreSQL 迁移
tools/idea_workflow.py        确定性 HTTP CLI
tools/e2e_lexical_rag.py      真实首次报告验收与 Citation 校验
development/followup-rag/     Corpus、首次报告/追问 RAG 的设计、实现日志和后续项
development/landscape/        专利态势重构需求、分类标准和开发规格
```

## License

MIT

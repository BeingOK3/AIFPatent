# LEXICAL_RAG 首次报告 MVP 设计

**日期：** 2026-07-22
**目标分支：** `develop`
**状态：** `ARCHIVED_IMPLEMENTED`

> 本文是词法 RAG 报告 MVP 的历史设计基线。该切片已经实现；项目随后又完成了 pgvector 语料基础设施、可选混合检索和追问 RAG。当前运行状态以 `development/followup-rag/README.md` 与 `development/followup-rag/architecture.md` 为准。

## 1. 目标

把当前 IDEA 首次评审升级为可直接部署和使用的 `LEXICAL_RAG` 报告闭环：深读专利全文形成不可变 Corpus Version 和结构化 Chunk，系统按 `F_i × D_j` 在源 Run 冻结范围内检索证据，强制覆盖摘要和全部独立权利要求，生成 JSON/Markdown 报告，并为每个实质性证据引用保存可验证 Citation。

GitHub `develop` 分支本身必须是可运行版本。新机器安装 Git、Docker Engine 和 Docker Compose 后，克隆仓库并执行 `./start.sh` 即可启动；`./stop.sh` 停止容器但保留数据。

## 2. 明确不在本次范围内

- 追问 Thread/Turn、聊天 API、SSE 和聊天 UI；
- Embedding、pgvector、RRF、Reranker 和语义检索；
- 精细 Citation 前端侧栏、高亮和交互展开；
- Variant、区别特征二次研究、权利要求对照表和法律状态；
- 用户、组织、租户或内容 ACL。

这些能力必须在 README 和 `development/followup-rag/development-log.md` 中明确标记为未完成，不得以占位接口暗示已经可用。

## 3. BYOK 与秘密边界

模型 API Base URL、模型名称和 API Key 由前端在创建 Run 时输入。它们只存在于当前页面内存和当前请求/运行时调用链：

- 刷新页面后消失；
- 不写入 SQLite、PostgreSQL、Redis、Docker Volume、报告、Manifest 或调试日志；
- 不进入 Git、Docker 镜像或 GitHub；
- 服务重启后不能恢复；
- 后端审计只记录非秘密的 provider/model/base URL 摘要和调用状态。

用户提供给开发者的测试密钥只通过端到端测试进程环境或请求体临时注入。测试结束后执行秘密模式扫描，并确认 Git diff、报告、日志和运行目录没有密钥残留。仓库不新增 `model.env`。

PostgreSQL、Redis、MinIO 的开发凭据由 `tools/rag_infra.py init` 随机生成到 Git 忽略且权限为 `0600` 的 `deploy/rag/rag.env`；这类基础设施凭据与模型 BYOK 完全分离。

## 4. 部署与默认状态

受支持的标准入口是 Docker Compose：

```bash
git clone -b develop git@github.com:BeingOK3/AIFPatent.git
cd AIFPatent
./start.sh
```

`start.sh` 必须：

1. 检查 Docker Engine、Compose 和磁盘余量；
2. 幂等创建私有 `rag.env`；
3. 构建并启动应用、PostgreSQL、Redis 和 MinIO；
4. 顺序执行全部增量迁移；
5. 幂等创建 Corpus Bucket；
6. 等待所有服务健康；
7. 输出本机访问地址和状态命令；
8. 不要求或读取模型 API Key。

`stop.sh` 只停止 Compose 服务，不删除命名卷、数据库、Corpus、报告或镜像。

仓库默认启用：

```text
patent_corpus = true
initial_review_rag = true
followup_rag = false
```

直接运行不完整的宿主机 Python 旧路径不再作为 README 的推荐入口。功能开启但 PostgreSQL、MinIO 或迁移不完整时启动必须 fail closed。

## 5. 报告证据架构

### 5.1 范围

首次报告检索只允许使用源 Run 中 `deep_reviewed=true` 且 `corpus_availability=READY` 的冻结 Version。允许集合从 PostgreSQL `run_document_versions` 获取，必须在检索 SQL 的 `WHERE` 层应用；不能只在 Prompt 中要求模型忽略范围外文献。

### 5.2 `F_i × D_j` 检索

新增共享的报告证据检索服务。对每个必需 IDEA Feature 和每篇深读专利分别执行：

1. 用 Feature 原文和受控中英文术语构造 lexical query；
2. 调用现有 `PostgreSQLLexicalSearchRepository`；
3. 仅返回目标 `version_id` 的 Chunk；
4. 保存原始 lexical rank、score、match kind、query ID 和 retriever version；
5. 按 Chunk ID 去重并保持确定性排序。

每个 Feature/Document 对必须有明确结果：命中、无证据或检索失败。无证据不能用其他文献补齐，也不能让模型依赖记忆。

### 5.3 强制权利要求覆盖

每篇深读专利的证据包必须包含：

- 摘要 Chunk；
- 每一项独立权利要求 Chunk；
- lexical query 命中的 Chunk；
- 命中从属权利要求时，其引用的父权利要求链。

缺少可识别独立权利要求、父链断裂、Chunk/Version 不一致或预算无法容纳最低强制集合时，该文档分析必须明确失败或降级，不得静默遗漏。

## 6. Context 与模型调用

现有 `ContextAssembler` 继续拥有模型上下文。报告管线为：

```text
Run frozen Versions
→ F_i × D_j lexical retrieval
→ forced abstract/independent claims/parent chains
→ deterministic Citation Packet
→ ContextAssembler
→ model-facing aliases C1..Cn
→ DeepSeek structured analysis
→ Citation Verifier
→ novelty/inventiveness/value/report composition
```

Context Manifest 至少记录：

- `run_id`、purpose=`INITIAL_REVIEW`、`corpus_snapshot_hash`；
- allowed Version IDs；
- prompt/retriever/context/tokenizer/chunker 版本；
- selected/excluded Chunk；
-输入预算、输出预留和实际使用量；
- Citation alias、Chunk ID、Version ID、text hash；
- `context_hash`。

完整专利正文不重复写入 Manifest、Checkpoint 或默认日志。LangChain 只负责消息类型适配，不拥有检索、裁剪、摘要或 Citation 语义。

## 7. Citation Contract

模型只能引用当前 Citation Packet 中的别名。持久 Citation 至少包含：

```json
{
  "alias": "C3",
  "publication_number": "CN123456789A",
  "version_id": "cv-...",
  "chunk_id": "...",
  "section_type": "claims",
  "section_label": "claim-1",
  "start_offset": 0,
  "end_offset": 328,
  "text_hash": "...",
  "excerpt": "..."
}
```

Citation Verifier 必须确定性验证：

- alias 存在于当前 Packet；
- Version 属于 allowed scope 且状态 READY；
- Chunk 属于该 Version；
- 公开号、章节、偏移和 text hash 与持久 Chunk 一致；
- excerpt 可从 Chunk 原文确定性得到；
- Feature mapping 的 `DISCLOSED`/`PARTIAL` 至少有一个有效 Citation；
- 不允许跨文献拼接新颖性。

任何未知 alias、错 Version、错 Chunk、哈希不一致或范围越界都必须拒绝保存分析结果。

## 8. 报告输出

JSON 报告升级 schema version，并在 Feature mapping/文档分析下包含结构化 Citation。Markdown 对每项实质结论展示：

```markdown
F1：已披露

依据：[C3] CN123456789A，权利要求1
原文：一种基于缓存热度……
```

Markdown 中的 excerpt 必须来自已验证 Chunk，不由报告模型改写。报告 Composer 仍只润色冻结事实，不能创建 Citation、公开号、日期或法律结论。

报告 Manifest 保存报告文件哈希、schema/workflow/retriever/prompt 版本和 `corpus_snapshot_hash`。生成后的报告与 Manifest 不可变。

## 9. 失败与降级

- Corpus/FTS 不可用：开启 `initial_review_rag` 时 fail closed；
- 某 Feature/Document 无命中：记录 `NO_LEXICAL_EVIDENCE`，但仍提供强制摘要/独立权利要求集合；
- 强制权利要求集合不完整：该文档结论降级或失败并写 limitation；
- Citation 校验失败：不保存该分析，不进入报告；
- 模型输出格式失败：服从现有结构化输出重试上限；
- embedding 不存在：合法的 `LEXICAL_RAG` 状态，不伪装成 Hybrid RAG；
- 外部 Provider 失败：沿用现有 limitation 和 minimum deep-review 门禁。

## 10. 测试与验收

所有行为修改使用 TDD。每个 Work Unit 必须完成定向测试、完整离线测试、`compileall`、`git diff --check`、秘密扫描、磁盘检查、代码审查、Git commit 和 `git push origin develop`。

最终验收包含：

1. 空白机器按 README 使用 Docker 从零启动；
2. 四个服务健康，迁移和 Bucket 初始化幂等；
3. 前端输入 Base URL、模型、API Key 后完成真实 quick Run；
4. 普通候选只保存元数据，深读文献均有 READY Version/Chunk；
5. 每个必需 `F_i × D_j` 有审计记录；
6. 摘要和全部独立权利要求被处理，父链完整；
7. JSON/Markdown Citation 通过 Version/Chunk/偏移/hash 校验；
8. 清空 FIFO Cache 后重新验证报告 Citation；
9. 报告和日志不含模型 API Key；
10. `followup_rag` 保持关闭，README 和开发日志列出所有未完成功能。

## 11. 实施单元与 Git 边界

按以下顺序连续实现，每个单元独立提交并推送：

1. `IDEA-REPORT-RETRIEVAL-001`：`F_i × D_j` 检索、审计 Repository 和 scope；
2. `IDEA-REPORT-CLAIMS-001`：摘要/独立权利要求/父链强制集合；
3. `IDEA-CONTEXT-CONTRACT-001`：PostgreSQL Context Manifest 和 Citation Packet；
4. `IDEA-REPORT-RAG-001`：接入 `ANALYZE_DOCUMENTS`；
5. `IDEA-REPORT-CITATION-001`：Citation Verifier、JSON/Markdown 输出；
6. `IDEA-RUNTIME-DEFAULT-001`：默认开关、一键启动、README 和未完成清单；
7. `IDEA-LEXICAL-RAG-E2E-001`：真实 DeepSeek、FIFO 清理、秘密扫描和从零部署验收。

任何单元审查发现 Critical/Important 问题时，修复并重新验证后才能提交和推送。开发期间根文件系统至少保留 5G 可用空间。

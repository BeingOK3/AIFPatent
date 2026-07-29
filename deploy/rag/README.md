# AIFPatent 本地/单机 RAG 栈

标准入口是仓库根目录的 `./start.sh` 与 `./stop.sh`。栈包含应用、PostgreSQL 17 + pgvector 0.8.2 和固定版本 MinIO，全部只绑定 `127.0.0.1`。

`start.sh` 会创建 Git 忽略且权限为 `0600` 的 `deploy/rag/rag.env`，生成本机随机基础设施凭证，先启动 PostgreSQL/MinIO，执行当前全部幂等迁移（含 Landscape `070`～`075`），再启动应用并确保 Bucket。这样应用启动钩子不会在旧 PostgreSQL 数据卷尚未迁移时查询新表。该文件不保存模型 Base URL、Model 或 API Key。

```bash
./start.sh
./stop.sh
```

`stop.sh` 不使用 `--volumes`，所以数据库、对象和应用数据均保留。项目没有自动删除卷的命令。

底层维护命令：

```bash
python3 tools/rag_infra.py status
python3 tools/rag_infra.py check
python3 tools/rag_infra.py migrate
python3 tools/rag_infra.py down
```

首次缺少对象存储镜像时会从固定 MinIO 源码版本构建；后续启动复用该镜像。只有升级 MinIO 构建定义时才显式执行：

```bash
AIFPATENT_REBUILD_OBJECT_STORE=1 ./start.sh
```

受限网络可在启动前设置非秘密构建镜像：

```bash
export AIFPATENT_PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
export AIFPATENT_GOPROXY=https://mirrors.tencent.com/go/,direct
./start.sh
```

默认构建已将 Python 依赖切换到腾讯云 PyPI 镜像，将 MinIO Go 依赖切换到腾讯云 Go 镜像；Docker/ PostgreSQL 基础镜像仍使用原始固定版本，不引入额外镜像仓库前提。需要使用其他软件源时，只修改 Git 忽略的 `rag.env`。

默认应用端口为 8001；需要避免本机冲突时，只修改 Git 忽略的 `rag.env` 中 `AIFPATENT_APP_PORT`。此 Compose 基线适合本地或单服务器经 SSH 隧道使用，不是公网、多 Worker、多租户生产部署。

## 可选部署级 Embedding

Embedding 默认关闭。启用时在本机 `rag.env` 增加 `AIFPATENT_EMBEDDING_ENABLED=true`、Provider/Model/Base URL/Dimensions 和 `EMBEDDING_API_KEY`；Compose 只把这些值传给 app，仓库配置与网页聊天 BYOK 均不修改。Base URL 必须能从 app 容器访问，不能把宿主机监听地址想当然地写成容器内的 `127.0.0.1`。

当前镜像不会复制 `tools/`，因此首次历史回填使用源码只读挂载：

```bash
docker compose --env-file deploy/rag/rag.env --file deploy/rag/compose.yml run \
  --rm --no-deps \
  --volume "$PWD:/workspace:ro" --workdir /workspace \
  --env PYTHONPATH=/workspace/backend \
  app python tools/index_embeddings.py
```

工具只输出 Profile/Version/Chunk 计数，不输出正文、DSN 或凭证。新 Corpus Chunk 在启用后会自动索引；任何批次失败时不会激活不完整 Profile。`/api/system/health` 只报告部署凭证是否存在，不回显其值。

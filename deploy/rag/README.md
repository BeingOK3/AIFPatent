# AIFPatent 本地/单机 LEXICAL_RAG 栈

标准入口是仓库根目录的 `./start.sh` 与 `./stop.sh`。栈包含应用、PostgreSQL 17 + pgvector 0.8.2、Redis 8.4.4 和固定版本 MinIO，全部只绑定 `127.0.0.1`。

`start.sh` 会创建 Git 忽略且权限为 `0600` 的 `deploy/rag/rag.env`，生成本机随机基础设施凭证，启动服务、确保 Bucket 并执行 `020/030/035` 增量迁移。该文件不保存模型 Base URL、Model 或 API Key。

```bash
./start.sh
./stop.sh
```

`stop.sh` 不使用 `--volumes`，所以数据库、对象、Redis 和应用数据均保留。项目没有自动删除卷的命令。

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
export AIFPATENT_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export AIFPATENT_GOPROXY=https://goproxy.cn,direct
./start.sh
```

默认应用端口为 8001；需要避免本机冲突时，只修改 Git 忽略的 `rag.env` 中 `AIFPATENT_APP_PORT`。此 Compose 基线适合本地或单服务器经 SSH 隧道使用，不是公网、多 Worker、多租户生产部署。

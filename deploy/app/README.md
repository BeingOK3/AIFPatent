# AIFPatent 应用镜像

`Dockerfile` 是当前混合持久化运行时的最小应用镜像：以固定 digest 的 Python 3.12 Bookworm 为基础，使用 UID/GID `10001` 运行单个 Uvicorn Worker。IDEA/Landscape 的 Run 与审计使用挂载卷中的 SQLite，Corpus/RAG 使用外部 PostgreSQL、Redis 和 S3/MinIO。镜像不包含数据库、用户工作区、日志或模型/检索凭证；这些内容必须由 Compose 或部署平台通过持久卷、环境和 Secret 提供。

在网络可以访问 PyPI 时构建：

```bash
docker buildx build \
  --file deploy/app/Dockerfile \
  --tag aifpatent-app:dev \
  --load .
```

受限网络可以只替换依赖下载源，不改变镜像内容：

```bash
docker buildx build \
  --allow network.host \
  --network host \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --file deploy/app/Dockerfile \
  --tag aifpatent-app:dev \
  --load .
```

应用镜像仍是单实例部署基线。当前 Run 执行和瞬时 BYOK 绑定单个应用进程，SQLite 也要求单写者语义，因此 Compose 固定为一个 Worker；多 Worker、多副本部署需要先迁移 Run 调度、凭证租约和 SQLite 业务事实，不能只调整 Uvicorn 参数。

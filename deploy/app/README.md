# AIFPatent 应用镜像

`Dockerfile` 是 PostgreSQL-only 运行时的最小应用镜像：以固定 digest 的 Python 3.12 Bookworm 为基础，使用 UID/GID `10001` 运行单个 Uvicorn Worker。IDEA、Landscape、Corpus/RAG、追问和工作流状态统一写入外部 PostgreSQL；大正文和产物写入 S3/MinIO。镜像不包含数据库、用户工作区、日志或模型/检索凭证；这些内容必须由 Compose 或部署平台通过持久卷、环境和 Secret 提供。

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
  --build-arg PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
  --file deploy/app/Dockerfile \
  --tag aifpatent-app:dev \
  --load .
```

应用镜像仍是单实例部署基线。当前 Run 执行和瞬时 BYOK 绑定单个应用进程，Compose 固定为一个 Worker；多 Worker、多副本部署仍需要先完成 PostgreSQL workflow lease、凭证租约和幂等协调，不能只调整 Uvicorn 参数。

# AIFPatent 应用镜像

`Dockerfile` 是当前 SQLite 运行时的最小应用镜像：以固定 digest 的 Python 3.12 Bookworm 为基础，使用 UID/GID `10001` 运行单个 Uvicorn Worker。它不包含数据库、用户工作区、日志或模型凭证；这些内容必须由 Compose 或部署平台通过持久卷/Secret 提供。

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

应用镜像仍是单实例部署基线；当前运行时使用 SQLite 和进程内任务管理，因此 Compose 和生产部署都固定为一个 Worker。

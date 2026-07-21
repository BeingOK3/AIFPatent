# Local application and RAG stack

This development stack provides the AIFPatent application plus PostgreSQL 17 + pgvector 0.8.2, Redis 8.4.4 and a locally built MinIO server. The application remains on its current SQLite runtime; RAG feature flags remain disabled until their migration gates are complete.

```bash
tools/rag_infra.py up
tools/rag_infra.py status
tools/rag_infra.py check
tools/rag_infra.py migrate
tools/rag_infra.py down
```

The first `up` creates `deploy/rag/rag.env` atomically with random credentials and mode `0600`. The file is Git ignored. Commands never print its values. `down` preserves named volumes; this tool intentionally has no reset or volume-deletion command.

The application is published only on `127.0.0.1:8001` by default and runs as a single non-root Worker. Its SQLite data, workspace and logs use separate named volumes. This is a local/single-instance deployment baseline, not a public multi-worker production deployment.

If PyPI is slow or unavailable, set `AIFPATENT_PIP_INDEX_URL` in the shell before `up`, for example `https://pypi.tuna.tsinghua.edu.cn/simple`; it only changes build-time dependency download.

`migrate` applies the additive PostgreSQL migrations to an already-created volume. This is needed because Docker runs files in `postgres-init/` only when the database volume is initialized; it never deletes or recreates the volume.

The MinIO community server is built from the pinned `RELEASE.2025-10-15T17-29-55Z` source tag rather than an older prebuilt image. The initial build therefore needs access to Go module sources. PostgreSQL initializes the `vector` and `pg_trgm` extensions only when its named volume is first created.

Ports bind to `127.0.0.1` by default and can be changed in `rag.env`. These credentials and services are for local development only.

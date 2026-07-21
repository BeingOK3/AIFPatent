from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Callable

from .object_store import ObjectStoreError
from .ports import ObjectInfo


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class S3ObjectStore:
    """Content-addressed ObjectStore adapter for S3-compatible services.

    boto3 is imported lazily so the local FileObjectStore and offline tests do
    not require cloud credentials or a running MinIO instance. Blocking SDK
    calls are isolated in worker threads to preserve the async domain API.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region_name: str = "us-east-1",
        client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
        run_in_thread: bool = True,
    ) -> None:
        for value, name in (
            (bucket, "bucket"),
            (endpoint_url, "endpoint_url"),
            (access_key, "access_key"),
            (secret_key, "secret_key"),
            (region_name, "region_name"),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} must not be empty")
        self.bucket = bucket
        self.endpoint_url = endpoint_url.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.region_name = region_name
        self._client = client
        self._client_factory = client_factory
        self._run_in_thread = run_in_thread

    async def _execute(self, operation: Callable[[], Any]) -> Any:
        if not self._run_in_thread:
            return operation()
        return await asyncio.to_thread(operation)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise ObjectStoreError("boto3 is required for S3ObjectStore") from exc
        factory = self._client_factory or boto3.client
        self._client = factory(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region_name,
        )
        return self._client

    @staticmethod
    def _validate_key(key: str) -> str:
        if not key or "\x00" in key or "\\" in key:
            raise ObjectStoreError("object key contains invalid characters")
        relative = PurePosixPath(key)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ObjectStoreError("object key must be a normalized relative path")
        return key

    @staticmethod
    def _hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _error_code(exc: Exception) -> str | None:
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            error = response.get("Error")
            if isinstance(error, dict):
                return str(error.get("Code") or "") or None
        return None

    @staticmethod
    def _metadata_value(metadata: Any, key: str) -> str | None:
        if not isinstance(metadata, dict):
            return None
        wanted = key.lower()
        for actual_key, value in metadata.items():
            if str(actual_key).lower() == wanted:
                return str(value) if value is not None else None
        return None

    def _info(self, key: str, response: dict[str, Any]) -> ObjectInfo:
        metadata = response.get("Metadata") or {}
        sha256 = self._metadata_value(metadata, "sha256")
        encoding = self._metadata_value(metadata, "encoding") or "identity"
        content_type = str(response.get("ContentType") or "application/octet-stream")
        if sha256 is None or not _SHA256.fullmatch(sha256):
            raise ObjectStoreError("S3 object is missing a valid immutable sha256 metadata value")
        return ObjectInfo(
            key=key,
            size=int(response.get("ContentLength", -1)),
            sha256=sha256,
            content_type=content_type,
            encoding=encoding,
        )

    async def put_if_absent(
        self,
        key: str,
        content: bytes,
        *,
        expected_sha256: str,
        content_type: str,
        encoding: str,
    ) -> ObjectInfo:
        self._validate_key(key)
        actual_sha256 = self._hash(content)
        if actual_sha256 != expected_sha256:
            raise ObjectStoreError("content hash does not match expected_sha256")
        if not _SHA256.fullmatch(expected_sha256):
            raise ObjectStoreError("expected_sha256 must be lowercase SHA-256")
        client = self._get_client()

        def operation() -> ObjectInfo:
            try:
                existing = client.head_object(Bucket=self.bucket, Key=key)
            except Exception as exc:
                if self._error_code(exc) not in {"404", "NoSuchKey", "NotFound"}:
                    raise ObjectStoreError("S3 object head request failed") from exc
                existing = None
            if existing is not None:
                info = self._info(key, existing)
                if info.sha256 != expected_sha256:
                    raise ObjectStoreError("immutable object key already contains different content")
                return info
            metadata = {"sha256": expected_sha256, "encoding": encoding}
            request: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": key,
                "Body": content,
                "ContentType": content_type,
                "Metadata": metadata,
                "IfNoneMatch": "*",
            }
            if encoding != "identity":
                request["ContentEncoding"] = encoding
            try:
                client.put_object(**request)
            except Exception as exc:
                if self._error_code(exc) not in {"PreconditionFailed", "412"}:
                    raise ObjectStoreError("S3 object write failed") from exc
            try:
                return self._info(key, client.head_object(Bucket=self.bucket, Key=key))
            except Exception as exc:
                raise ObjectStoreError("S3 object cannot be verified after write") from exc

        return await self._execute(operation)

    async def get(self, key: str) -> bytes:
        self._validate_key(key)
        client = self._get_client()

        def operation() -> bytes:
            try:
                response = client.get_object(Bucket=self.bucket, Key=key)
                body = response["Body"].read()
            except Exception as exc:
                raise ObjectStoreError(f"object does not exist: {key}") from exc
            expected = self._metadata_value(response.get("Metadata"), "sha256")
            if expected is None or self._hash(body) != expected:
                raise ObjectStoreError("S3 object content hash verification failed")
            return body

        return await self._execute(operation)

    async def stat(self, key: str) -> ObjectInfo | None:
        self._validate_key(key)
        client = self._get_client()

        def operation() -> ObjectInfo | None:
            try:
                response = client.head_object(Bucket=self.bucket, Key=key)
            except Exception as exc:
                if self._error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise ObjectStoreError("S3 object head request failed") from exc
            return self._info(key, response)

        return await self._execute(operation)

    async def healthcheck(self) -> dict[str, Any]:
        client = self._get_client()

        def operation() -> dict[str, Any]:
            try:
                client.head_bucket(Bucket=self.bucket)
            except Exception as exc:
                raise ObjectStoreError("S3 bucket healthcheck failed") from exc
            return {"ok": True, "bucket": self.bucket, "endpoint": self.endpoint_url}

        return await self._execute(operation)


__all__ = ["S3ObjectStore"]

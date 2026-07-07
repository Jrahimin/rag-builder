"""MinIO / S3-compatible object storage via boto3."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

import boto3
from botocore.exceptions import ClientError

from app.core.config import MinioConfig
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderAuthenticationError, ProviderError

_T = TypeVar("_T")


class MinioStorageProvider(BaseStorageProvider):
    """S3-compatible storage backed by MinIO (or AWS S3 with adjusted endpoint)."""

    def __init__(self, config: MinioConfig) -> None:
        self._config = config
        self._client = boto3.client(
            "s3",
            endpoint_url=config.url,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name=config.region,
            use_ssl=config.secure,
        )

    async def _run(self, func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        return await asyncio.to_thread(func, *args, **kwargs)

    async def put(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        *,
        content_type: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        body = bytearray()
        async for chunk in stream:
            body.extend(chunk)

        extra_args: dict[str, str] = {}
        if content_type:
            extra_args["ContentType"] = content_type

        try:
            await self._run(
                self._client.put_object,
                Bucket=self._config.bucket,
                Key=key,
                Body=bytes(body),
                **extra_args,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch"}:
                raise ProviderAuthenticationError(
                    "MinIO authentication failed.",
                    provider_name="minio",
                ) from exc
            raise ProviderError(
                f"Failed to store object: {key}",
                provider_name="minio",
                context={"size_bytes": size_bytes or len(body)},
            ) from exc

    async def get(self, key: str) -> AsyncIterator[bytes]:
        try:
            response = await self._run(
                self._client.get_object,
                Bucket=self._config.bucket,
                Key=key,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                msg = f"Object not found: {key}"
                raise ProviderError(msg, provider_name="minio") from exc
            raise ProviderError(
                f"Failed to read object: {key}",
                provider_name="minio",
            ) from exc

        body: bytes = response["Body"].read()
        chunk_size = 64 * 1024
        for offset in range(0, len(body), chunk_size):
            yield body[offset : offset + chunk_size]

    async def delete(self, key: str) -> None:
        try:
            await self._run(
                self._client.delete_object,
                Bucket=self._config.bucket,
                Key=key,
            )
        except ClientError as exc:
            raise ProviderError(
                f"Failed to delete object: {key}",
                provider_name="minio",
            ) from exc

    async def delete_document_tree(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        prefix = f"{project_id}/{document_id}/"
        continuation_token: str | None = None
        while True:
            kwargs: dict[str, str] = {
                "Bucket": self._config.bucket,
                "Prefix": prefix,
            }
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            response = await self._run(self._client.list_objects_v2, **kwargs)
            contents = response.get("Contents") or []
            if contents:
                await self._run(
                    self._client.delete_objects,
                    Bucket=self._config.bucket,
                    Delete={
                        "Objects": [{"Key": item["Key"]} for item in contents],
                        "Quiet": True,
                    },
                )
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

    async def get_download_url(self, key: str, *, expires_seconds: int = 3600) -> str | None:
        try:
            url = await self._run(
                self._client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._config.bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
        except ClientError as exc:
            raise ProviderError(
                f"Failed to generate download URL: {key}",
                provider_name="minio",
            ) from exc
        return str(url)

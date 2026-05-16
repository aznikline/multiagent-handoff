"""S3-backed storage for context packages.

Requires ``aiobotocore``. Sync boto3 is **not** supported because all
store methods are async.

The expected ``s3_client`` is an aiobotocore ``AioBaseClient`` obtained
via ``session.create_client("s3", ...)``.

Note: S3 support is functional but not yet covered by automated integration
tests against a real S3 endpoint. Use with appropriate validation.
"""

from __future__ import annotations

from typing import Any

from handoff.models.package import ContextPackage
from handoff.orchestrator.store import HandoffStore, StoreError
from handoff.serialization.serializer import JsonSerializer


class S3HandoffStore(HandoffStore):
    """S3 store using aiobotocore.

    Requires ``aiobotocore``. For async support,
    aiobotocore is **required**; sync boto3 will not work.

    Expected client interface (aiobotocore AioBaseClient):
        - await client.put_object(Bucket=..., Key=..., Body=..., ...)
        - response = await client.get_object(Bucket=..., Key=...)
          data = await response["Body"].read()
        - await client.delete_object(Bucket=..., Key=...)
        - url = await client.generate_presigned_url(...)
    """

    def __init__(
        self,
        bucket: str,
        s3_client: Any,
        key_prefix: str = "handoff/packages/",
        sse: str | None = "AES256",
    ) -> None:
        super().__init__()
        self._bucket = bucket
        self._s3 = s3_client
        self._prefix = key_prefix
        self._sse = sse
        self._serializer = JsonSerializer()

    def _key(self, package_id: str) -> str:
        return f"{self._prefix}{package_id}.json"

    async def save(self, package: ContextPackage) -> None:
        try:
            payload = self._serializer.serialize(package).decode("utf-8")
            key = self._key(package.meta.package_id)
            extra_args: dict[str, Any] = {"ContentType": "application/json"}
            if self._sse:
                extra_args["ServerSideEncryption"] = self._sse
            await self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                **extra_args,
            )
        except Exception as exc:
            raise StoreError(f"S3 save failed: {exc}") from exc

    async def load(self, package_id: str) -> ContextPackage | None:
        try:
            key = self._key(package_id)
            response = await self._s3.get_object(Bucket=self._bucket, Key=key)
            # aiobotocore StreamingBody supports direct .read()
            data: bytes = await response["Body"].read()
            return self._serializer.deserialize(data)
        except Exception as exc:
            # Check for 404
            if hasattr(exc, "response") and exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                return None
            raise StoreError(f"S3 load failed: {exc}") from exc

    async def delete(self, package_id: str) -> bool:
        try:
            key = self._key(package_id)
            await self._s3.delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as exc:
            raise StoreError(f"S3 delete failed: {exc}") from exc

    async def list_expired(self) -> list[str]:
        """S3 does not natively support TTL expiry.

        Use S3 Lifecycle policies or implement a separate sweeper.
        Returns empty list for interface compliance.
        """
        return []

    async def generate_presigned_url(
        self, package_id: str, expiration: int = 3600
    ) -> str:
        """Generate a presigned URL for secure package retrieval.

        Args:
            package_id: Package to generate URL for.
            expiration: URL expiry in seconds (default 1 hour).

        Returns:
            Presigned HTTPS URL.
        """
        try:
            key = self._key(package_id)
            url: str = await self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expiration,
            )
            return url
        except Exception as exc:
            raise StoreError(f"Failed to generate presigned URL: {exc}") from exc

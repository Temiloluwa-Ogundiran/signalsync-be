"""
Lazy-initialized AWS S3 client (boto3). Private bucket; reads are served via
short-lived presigned GET URLs. This is the single object-storage backend.
"""

from __future__ import annotations

from threading import Lock

import boto3
from botocore.client import Config as BotoConfig

from app.core.config import settings

_s3_client = None
_lock = Lock()


def s3_enabled() -> bool:
    """True when S3 storage is configured (bucket name present)."""
    return bool((settings.AWS_S3_BUCKET or "").strip())


def get_s3_client():
    """Return a singleton boto3 S3 client, initializing it on first call."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    with _lock:
        if _s3_client is None:
            if not s3_enabled():
                raise RuntimeError(
                    "AWS_S3_BUCKET is not configured; object storage is unavailable."
                )
            kwargs = {
                "region_name": (settings.AWS_S3_REGION or "us-east-1").strip(),
                # Use SigV4 so presigned URLs work in every region.
                "config": BotoConfig(signature_version="s3v4"),
            }
            access_key = (settings.AWS_ACCESS_KEY_ID or "").strip()
            secret_key = (settings.AWS_SECRET_ACCESS_KEY or "").strip()
            if access_key and secret_key:
                kwargs["aws_access_key_id"] = access_key
                kwargs["aws_secret_access_key"] = secret_key
            endpoint = (settings.AWS_S3_ENDPOINT_URL or "").strip()
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            # If keys are absent, boto3 falls back to the default credential
            # chain (IAM role / instance profile) — fine in production.
            _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def s3_bucket() -> str:
    bucket = (settings.AWS_S3_BUCKET or "").strip()
    if not bucket:
        raise RuntimeError("AWS_S3_BUCKET is not configured.")
    return bucket


def put_object(*, key: str, body: bytes, content_type: str) -> None:
    """Upload bytes to the bucket under `key`."""
    get_s3_client().put_object(
        Bucket=s3_bucket(),
        Key=key,
        Body=body,
        ContentType=content_type or "application/octet-stream",
    )


def presigned_get_url(*, key: str, expires_in: int) -> str:
    """Return a short-lived presigned GET URL for `key`."""
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": s3_bucket(), "Key": key},
        ExpiresIn=max(1, int(expires_in)),
    )


def delete_object(*, key: str) -> bool:
    """Best-effort delete of `key`. Returns True on success, never raises."""
    try:
        get_s3_client().delete_object(Bucket=s3_bucket(), Key=key)
        return True
    except Exception:
        return False


def delete_objects(keys: list[str]) -> int:
    """Best-effort bulk delete. Returns the number of keys requested for delete.

    S3 delete_objects takes up to 1000 keys per call; never raises.
    """
    cleaned: list[str] = [k for k in keys if k]
    if not cleaned:
        return 0
    client = get_s3_client()
    bucket = s3_bucket()
    deleted = 0
    for i in range(0, len(cleaned), 1000):
        chunk = cleaned[i : i + 1000]
        try:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )
            deleted += len(chunk)
        except Exception:
            continue
    return deleted


# Reset hook for tests.
def _reset_client_for_tests() -> None:  # pragma: no cover
    global _s3_client
    _s3_client = None

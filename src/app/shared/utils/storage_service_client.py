"""
HTTP client for the Syncgram storage microservice (multipart upload → private S3, read via /files/…).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, UploadFile, status

from app.core.config import settings


def storage_service_enabled() -> bool:
    return bool((settings.STORAGE_SERVICE_BASE_URL or "").strip())


def normalized_storage_service_base_url() -> str:
    return (settings.STORAGE_SERVICE_BASE_URL or "").strip().rstrip("/")


def is_legacy_supabase_storage_path(storage_path: str) -> bool:
    """
    Paths written by Supabase uploads used `{prefix}/{uuid}.{ext}` and always contain '/'.
    The storage microservice returns a single-segment object key (e.g. `uuid.ext`).
    """
    return "/" in storage_path


def gateway_url_for_object_key(object_key: str) -> str:
    base = normalized_storage_service_base_url()
    if not base:
        raise RuntimeError("STORAGE_SERVICE_BASE_URL is not configured.")
    encoded = quote(object_key, safe="/")
    return f"{base}/files/{encoded}"


def upload_via_storage_service(file: UploadFile, *, timeout_seconds: float = 120.0) -> str:
    """
    POST multipart to the storage service; returns the object key (`filename` in the API JSON).

    Raises:
        HTTPException on transport errors or invalid responses.
    """
    base = normalized_storage_service_base_url()
    if not base:
        raise RuntimeError("STORAGE_SERVICE_BASE_URL is not configured.")

    path = settings.STORAGE_SERVICE_UPLOAD_PATH or "/api/upload"
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base}{path}"

    file.file.seek(0)
    body = file.file.read()
    upload_name = file.filename or "upload.bin"
    content_type = file.content_type or "application/octet-stream"

    headers: dict[str, str] = {}
    api_key = (settings.STORAGE_SERVICE_API_KEY or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                url,
                headers=headers or None,
                files={"file": (upload_name, body, content_type)},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage service rejected the upload.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the storage service.",
        ) from exc

    try:
        data: dict[str, Any] = response.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage service returned invalid JSON.",
        ) from exc

    if not data.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage service upload was not successful.",
        )
    object_key = data.get("filename")
    if not object_key or not isinstance(object_key, str):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage service response missing filename.",
        )
    return object_key

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings


async def read_upload_within_limit(
    file: UploadFile,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Read an UploadFile into memory, rejecting anything over the size limit.

    Rejects on the declared size first (cheap, no read) when available, then reads
    in bounded chunks and aborts as soon as the limit is exceeded — so an oversized
    or lying upload can never buffer unbounded memory.
    """
    limit = max_bytes if max_bytes is not None else settings.MAX_UPLOAD_BYTES

    declared = getattr(file, "size", None)
    if declared is not None and declared > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds the maximum allowed size of {limit // (1024 * 1024)} MB.",
        )

    chunks: list[bytes] = []
    total = 0
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds the maximum allowed size of {limit // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)

    return b"".join(chunks)

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

# Magic-byte signatures for allowed upload types.
# Each entry is (name, prefix_bytes).  Only the first N bytes of the file are
# checked, so we keep minimal signatures to avoid false positives.
_IMAGE_SIGNATURES: list[tuple[str, bytes]] = [
    ("JPEG", b"\xff\xd8\xff"),
    ("PNG", b"\x89PNG\r\n\x1a\n"),
    ("GIF", b"GIF87a"),
    ("GIF", b"GIF89a"),
    ("WebP", b"RIFF"),  # full check below
]

_AUDIO_SIGNATURES: list[tuple[str, bytes]] = [
    ("MP3 (ID3)", b"ID3"),
    ("MP3", b"\xff\xfb"),
    ("MP3", b"\xff\xf3"),
    ("MP3", b"\xff\xf2"),
    ("OGG", b"OggS"),
    ("WEBM/OPUS", b"\x1a\x45\xdf\xa3"),
    ("WAV", b"RIFF"),  # RIFF + WAVE fourcc — extra check in validate_audio_magic_bytes
    ("FLAC", b"fLaC"),
    # M4A/AAC: ISO Base Media File Format (ftyp box at offset 4)
    # Header: [4-byte box size][b"ftyp"]
    # We match the literal "ftyp" at offset 4 (bytes 4-7) via a separate path below.
]

_FTYP_OFFSET = 4
_FTYP_MAGIC = b"ftyp"


def _matches_any(data: bytes, signatures: list[tuple[str, bytes]]) -> bool:
    return any(data[:len(sig)] == sig for _, sig in signatures)


def validate_image_magic_bytes(data: bytes) -> None:
    """Raise 415 if the byte sequence does not match a known image format."""
    if not _matches_any(data, _IMAGE_SIGNATURES):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Uploaded file does not appear to be a supported image (JPEG, PNG, GIF, WebP).",
        )
    # Extra check: RIFF must be followed by 4-byte size then "WEBP"
    if data[:4] == b"RIFF" and data[8:12] != b"WEBP":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="RIFF file is not a WebP image.",
        )


def validate_audio_magic_bytes(data: bytes) -> None:
    """Raise 415 if the byte sequence does not match a known audio format."""
    # Check common header signatures first
    if _matches_any(data, _AUDIO_SIGNATURES):
        # Extra check: RIFF must be followed by 4-byte size then "WAVE"
        if data[:4] == b"RIFF" and data[8:12] != b"WAVE":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="RIFF file is not a WAV audio file.",
            )
        return
    # Check ISO Base Media (M4A/MP4 audio): ftyp box at offset 4
    if len(data) >= _FTYP_OFFSET + 4 and data[_FTYP_OFFSET:_FTYP_OFFSET + 4] == _FTYP_MAGIC:
        return
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Uploaded file does not appear to be a supported audio format.",
    )


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

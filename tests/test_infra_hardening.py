import io
import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException

# Import domain models so SQLAlchemy mappers resolve when the app is imported.
import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.shared.utils.uploads import read_upload_within_limit


class _FakeUpload:
    """Minimal UploadFile stand-in exposing .size and async .read()."""

    def __init__(self, data: bytes, size: int | None = None):
        self._buf = io.BytesIO(data)
        self.size = size

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


@pytest.mark.anyio
async def test_read_upload_rejects_oversized_by_declared_size() -> None:
    upload = _FakeUpload(b"x" * 10, size=999_999_999)
    with pytest.raises(HTTPException) as exc:
        await read_upload_within_limit(upload, max_bytes=1024)
    assert exc.value.status_code == 413


@pytest.mark.anyio
async def test_read_upload_rejects_oversized_while_streaming() -> None:
    # size unknown (None) -> must still abort once the byte limit is exceeded.
    upload = _FakeUpload(b"x" * 5000, size=None)
    with pytest.raises(HTTPException) as exc:
        await read_upload_within_limit(upload, max_bytes=1024)
    assert exc.value.status_code == 413


@pytest.mark.anyio
async def test_read_upload_returns_bytes_within_limit() -> None:
    upload = _FakeUpload(b"hello world", size=11)
    data = await read_upload_within_limit(upload, max_bytes=1024)
    assert data == b"hello world"


def test_health_endpoint_sets_request_id_header() -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.shared.request_id import REQUEST_ID_HEADER

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers.get(REQUEST_ID_HEADER)


def test_start_api_treats_blank_web_concurrency_as_default(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "docker_start", Path(__file__).resolve().parents[1] / "scripts" / "docker_start.py"
    )
    assert spec is not None and spec.loader is not None
    docker_start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docker_start)

    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(docker_start, "wait_for_deps", lambda: None)
    monkeypatch.setattr(docker_start.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(docker_start, "_default_workers", lambda: "3")
    monkeypatch.setenv("WEB_CONCURRENCY", "")

    def fake_execvp(program: str, argv: list[str]) -> None:
        captured["argv"] = argv
        raise SystemExit

    monkeypatch.setattr(docker_start.os, "execvp", fake_execvp)

    with pytest.raises(SystemExit):
        docker_start.start_api()

    workers_index = captured["argv"].index("--workers") + 1
    assert captured["argv"][workers_index] == "3"

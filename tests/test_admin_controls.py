import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.domains.admin import service
from app.domains.admin.deps import require_admin, require_technical_admin
from app.domains.admin.metrics import render_metrics
from app.domains.admin.schemas import ProductEventCreate
from app.domains.users.models import PlatformRole


def user(role: PlatformRole, *, user_id: uuid.UUID | None = None):
    return SimpleNamespace(id=user_id or uuid.uuid4(), platform_role=role)


def request_with_token(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
    )


def test_admin_and_technical_permissions_are_separate():
    assert require_admin(current_user=user(PlatformRole.ADMIN)).platform_role == PlatformRole.ADMIN
    assert (
        require_technical_admin(current_user=user(PlatformRole.TECHNICAL_ADMIN)).platform_role
        == PlatformRole.TECHNICAL_ADMIN
    )
    with pytest.raises(HTTPException) as exc:
        require_technical_admin(current_user=user(PlatformRole.ADMIN))
    assert exc.value.status_code == 403


def test_regular_admin_cannot_manage_privileged_account():
    with pytest.raises(HTTPException) as exc:
        service._assert_can_manage(
            user(PlatformRole.ADMIN), user(PlatformRole.TECHNICAL_ADMIN)
        )
    assert exc.value.status_code == 403


def test_super_admin_cannot_modify_self():
    identity = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        service._assert_can_manage(
            user(PlatformRole.SUPER_ADMIN, user_id=identity),
            user(PlatformRole.USER, user_id=identity),
        )
    assert exc.value.status_code == 409


def test_product_events_require_timezone_and_whitelisted_metadata():
    payload = ProductEventCreate(
        session_id=uuid.uuid4(),
        event_name="page_view",
        path="/dashboard",
        metadata={"device": "desktop"},
        occurred_at=datetime.now(timezone.utc),
    )
    assert payload.event_name == "page_view"
    with pytest.raises(ValueError):
        ProductEventCreate(
            session_id=uuid.uuid4(),
            event_name="arbitrary_event",
            path="/",
            occurred_at=datetime.now(timezone.utc),
        )


def test_metrics_endpoint_requires_configured_bearer_token(monkeypatch):
    monkeypatch.setattr("app.domains.admin.metrics.settings.METRICS_BEARER_TOKEN", "x" * 32)
    monkeypatch.setattr("app.domains.admin.metrics.settings.MT5_CORE_INTERNAL_SHARED_SECRET", "")
    with pytest.raises(HTTPException) as exc:
        render_metrics(request_with_token("wrong"))
    assert exc.value.status_code == 401
    response = render_metrics(request_with_token("x" * 32))
    assert response.status_code == 200
    assert b"tradepartna_http_requests_total" in response.body


def test_metrics_endpoint_accepts_existing_service_secret(monkeypatch):
    monkeypatch.setattr("app.domains.admin.metrics.settings.METRICS_BEARER_TOKEN", "x" * 32)
    monkeypatch.setattr(
        "app.domains.admin.metrics.settings.MT5_CORE_INTERNAL_SHARED_SECRET",
        "internal-service-secret",
    )
    response = render_metrics(request_with_token("internal-service-secret"))
    assert response.status_code == 200


def worker_heartbeat(role: str, *, age_seconds: int = 0):
    return SimpleNamespace(
        worker_role=role,
        state=SimpleNamespace(value="healthy"),
        heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        stream_lag=0,
        pending_count=0,
        last_error=None,
    )


def test_system_overview_ignores_retired_worker_roles(monkeypatch):
    required = (
        "telegram-session",
        "copy-signal",
        "copy-execution",
        "copy-provisioning",
    )
    heartbeats = [worker_heartbeat(role) for role in required]
    heartbeats.append(worker_heartbeat("copy-learning", age_seconds=86400))
    monkeypatch.setattr(service.repo, "list_latest_worker_health", lambda _: heartbeats)
    monkeypatch.setattr(service.repo, "recent_copy_metrics", lambda _: [])

    result = service.system_overview(SimpleNamespace())

    assert result.status == "healthy"
    assert [item["name"] for item in result.components] == list(required)


def test_system_overview_reports_missing_required_worker(monkeypatch):
    monkeypatch.setattr(service.repo, "list_latest_worker_health", lambda _: [])
    monkeypatch.setattr(service.repo, "recent_copy_metrics", lambda _: [])

    result = service.system_overview(SimpleNamespace())

    assert result.status == "degraded"
    assert all(item["status"] == "missing" for item in result.components)

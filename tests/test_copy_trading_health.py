from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.copy_trading.health import REQUIRED_WORKER_ROLES, aggregate_health


NOW = datetime(2026, 6, 22, tzinfo=timezone.utc)


def heartbeat(role: str, age: int = 0, state: str = "healthy"):
    return SimpleNamespace(
        worker_role=role,
        instance_id=f"{role}-1",
        heartbeat_at=NOW - timedelta(seconds=age),
        state=SimpleNamespace(value=state),
        stream_lag=0,
        pending_count=0,
        last_error=None,
    )


def test_all_required_workers_must_be_fresh() -> None:
    result = aggregate_health(
        [
            heartbeat("telegram-session"),
            heartbeat("copy-learning"),
            heartbeat("copy-signal"),
            heartbeat("copy-execution"),
        ],
        now=NOW,
    )

    assert result.status == "ready"
    assert result.ready is True


def test_stale_execution_worker_makes_health_degraded() -> None:
    result = aggregate_health(
        [
            heartbeat("telegram-session"),
            heartbeat("copy-learning"),
            heartbeat("copy-signal"),
            heartbeat("copy-execution", age=91),
        ],
        now=NOW,
    )

    assert result.status == "degraded"
    assert result.ready is False
    assert any("copy-execution" in issue for issue in result.issues)


def test_missing_worker_is_reported_as_action_required() -> None:
    result = aggregate_health([], now=NOW)

    assert result.status == "action_required"
    assert len(result.issues) == 3


def test_channel_learning_worker_is_not_required_for_runtime_health() -> None:
    assert "copy-learning" not in REQUIRED_WORKER_ROLES

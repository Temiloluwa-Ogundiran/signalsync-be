from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.copy_trading.health import (
    REQUIRED_WORKER_ROLES,
    aggregate_health,
    build_launch_readiness,
    add_metaapi_health,
)


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
            heartbeat("copy-signal"),
            heartbeat("copy-execution"),
            heartbeat("copy-provisioning"),
        ],
        now=NOW,
    )

    assert result.status == "ready"
    assert result.ready is True


def test_stale_execution_worker_makes_health_degraded() -> None:
    result = aggregate_health(
        [
            heartbeat("telegram-session"),
            heartbeat("copy-signal"),
            heartbeat("copy-execution", age=91),
            heartbeat("copy-provisioning"),
        ],
        now=NOW,
    )

    assert result.status == "degraded"
    assert result.ready is False
    assert any("copy-execution" in issue for issue in result.issues)


def test_missing_worker_is_reported_as_action_required() -> None:
    result = aggregate_health([], now=NOW)

    assert result.status == "action_required"
    assert len(result.issues) == len(REQUIRED_WORKER_ROLES)


def test_channel_learning_worker_is_not_required_for_runtime_health() -> None:
    assert "copy-learning" not in REQUIRED_WORKER_ROLES


def test_metaapi_configuration_is_required_when_copy_trading_is_enabled() -> None:
    health = aggregate_health(
        [heartbeat(role) for role in REQUIRED_WORKER_ROLES], now=NOW
    )

    result = add_metaapi_health(
        health,
        copy_trading_enabled=True,
        metaapi_enabled=True,
        token_configured=False,
    )

    assert result.ready is False
    assert result.status == "action_required"
    assert result.components[-1]["role"] == "metaapi"
    assert result.components[-1]["status"] == "missing"


def test_launch_readiness_blocks_old_uncertain_intent() -> None:
    result = build_launch_readiness(
        aggregate_health(
            [heartbeat(role) for role in REQUIRED_WORKER_ROLES],
            now=NOW,
        ),
        dead_letter_count=0,
        uncertain_intent_ages=[121],
        active_intent_ages=[],
        active_intent_max_age_seconds=60,
        uncertain_max_age_seconds=60,
        global_paused=True,
    )

    assert result.ready is False
    assert "uncertain_intents" in result.blockers
    assert result.oldest_uncertain_seconds == 121


def test_launch_readiness_blocks_pending_dead_letters() -> None:
    result = build_launch_readiness(
        aggregate_health(
            [heartbeat(role) for role in REQUIRED_WORKER_ROLES],
            now=NOW,
        ),
        dead_letter_count=2,
        uncertain_intent_ages=[],
        active_intent_ages=[],
        active_intent_max_age_seconds=60,
        uncertain_max_age_seconds=60,
        global_paused=True,
    )

    assert result.ready is False
    assert "dead_letters" in result.blockers


def test_launch_readiness_blocks_stuck_active_intent() -> None:
    result = build_launch_readiness(
        aggregate_health(
            [heartbeat(role) for role in REQUIRED_WORKER_ROLES],
            now=NOW,
        ),
        dead_letter_count=0,
        uncertain_intent_ages=[],
        active_intent_ages=[61],
        active_intent_max_age_seconds=60,
        uncertain_max_age_seconds=60,
        global_paused=True,
    )

    assert result.ready is False
    assert "active_intents" in result.blockers


def test_launch_readiness_can_be_ready_while_globally_paused() -> None:
    result = build_launch_readiness(
        aggregate_health(
            [heartbeat(role) for role in REQUIRED_WORKER_ROLES],
            now=NOW,
        ),
        dead_letter_count=0,
        uncertain_intent_ages=[],
        active_intent_ages=[],
        active_intent_max_age_seconds=60,
        uncertain_max_age_seconds=60,
        global_paused=True,
    )

    assert result.ready is True
    assert result.global_paused is True

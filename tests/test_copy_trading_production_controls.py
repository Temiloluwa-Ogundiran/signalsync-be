import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from app.domains.copy_trading.metaapi_execution import _notify_execution
from app.domains.copy_trading.telemetry import record_execution_metric
from app.domains.copy_trading.workers import _semantic_fingerprint


def test_semantic_duplicate_fingerprint_ignores_delivery_metadata() -> None:
    route = SimpleNamespace(id=uuid.uuid4())
    signal = {"action": "open_market", "symbol": "EURUSD", "direction": "buy", "stop_loss": "1.1", "take_profits": ["1.2"]}

    first = _semantic_fingerprint(route, {**signal, "message_id": 10})
    second = _semantic_fingerprint(route, {**signal, "message_id": 11})
    changed = _semantic_fingerprint(route, {**signal, "direction": "sell"})

    assert first == second
    assert changed != first


@patch("app.domains.copy_trading.metaapi_execution.notify")
def test_disabled_route_notification_produces_no_delivery(notify) -> None:
    db = MagicMock()
    route = SimpleNamespace(notify_success=False, notify_failure=False, user_id=uuid.uuid4())

    _notify_execution(db, route=route, title="Done", body="Body", success=True, details={})
    _notify_execution(db, route=route, title="Failed", body="Body", success=False, details={})

    notify.assert_not_called()
    db.get.assert_not_called()


@patch("app.tasks.copy_trading_tasks.send_execution_email_task")
@patch("app.domains.copy_trading.metaapi_execution.notify")
def test_enabled_route_email_is_enqueued_without_waiting_for_a_result(notify, email_task) -> None:
    db = MagicMock()
    user_id = uuid.uuid4()
    db.get.return_value = SimpleNamespace(email="trader@example.com")
    route = SimpleNamespace(notify_success=True, notify_failure=True, user_id=user_id)

    _notify_execution(db, route=route, title="Done", body="Body", success=True, details={"symbol": "EURUSD"})

    notify.assert_called_once()
    db.get.assert_called_once_with(ANY, user_id)
    email_task.apply_async.assert_called_once_with(
        args=("trader@example.com", "Done", {"symbol": "EURUSD"}),
        ignore_result=True,
    )


def test_execution_metric_records_each_pipeline_stage() -> None:
    telegram_at = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)
    ingested_at = telegram_at + timedelta(milliseconds=100)
    validated_at = ingested_at + timedelta(milliseconds=200)
    submitted_at = validated_at + timedelta(milliseconds=50)
    resolved_at = submitted_at + timedelta(milliseconds=300)
    intent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        submitted_at=submitted_at,
        resolved_at=resolved_at,
        request_payload={
            "action": "open_market",
            "symbol": "EURUSD",
            "_telemetry": {
                "correlation_id": "signal-1",
                "telegram_at": telegram_at.isoformat(),
                "ingested_at": ingested_at.isoformat(),
                "validated_at": validated_at.isoformat(),
            },
        },
    )
    route = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    metric = record_execution_metric(db, intent=intent, route=route, status="confirmed")

    assert metric.ingestion_ms == 100
    assert metric.assembly_ms == 200
    assert metric.broker_ms == 300
    assert metric.total_ms == 650
    db.add.assert_called_once_with(metric)

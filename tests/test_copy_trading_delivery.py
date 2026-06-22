from unittest.mock import MagicMock
import uuid

from app.domains.copy_trading.delivery import DeliveryResult
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName
from app.domains.copy_trading.worker_runtime import StreamWorker


def event() -> CopyEvent:
    return CopyEvent.new(
        stream=StreamName.telegram_messages,
        event_type="message.created",
        correlation_id="delivery-test",
        payload={"source_id": str(uuid.uuid4())},
        idempotency_key="delivery:1",
    )


def worker_for(result: DeliveryResult) -> StreamWorker:
    worker = object.__new__(StreamWorker)
    worker.client = MagicMock()
    worker.client.get.return_value = None
    worker.stream = StreamName.telegram_messages
    worker.group = "copy-signal"
    worker.handler = MagicMock(return_value=result)
    worker._pending_attempts = MagicMock(return_value=1)
    worker._persist_dead_letter = MagicMock()
    return worker


def test_success_is_deduplicated_and_acknowledged() -> None:
    worker = worker_for(DeliveryResult.success())
    message = event()

    worker._process_message("1-0", message.to_fields())

    worker.client.setex.assert_called_once()
    worker.client.xack.assert_called_once_with(
        StreamName.telegram_messages.value, "copy-signal", "1-0"
    )


def test_transient_result_remains_pending_for_retry() -> None:
    worker = worker_for(DeliveryResult.retry("OPENAI_TIMEOUT", "try again"))

    worker._process_message("1-0", event().to_fields())

    worker.client.setex.assert_not_called()
    worker.client.xack.assert_not_called()
    worker._persist_dead_letter.assert_not_called()


def test_permanent_result_is_persisted_then_acknowledged() -> None:
    worker = worker_for(DeliveryResult.dead_letter("INVALID_EVENT", "bad payload"))

    worker._process_message("1-0", event().to_fields())

    worker._persist_dead_letter.assert_called_once()
    worker.client.xack.assert_called_once()
    worker.client.setex.assert_not_called()


def test_retry_exhaustion_becomes_a_dead_letter() -> None:
    worker = worker_for(DeliveryResult.retry("OPENAI_TIMEOUT", "try again"))
    worker._pending_attempts.return_value = worker.max_attempts

    worker._process_message("1-0", event().to_fields())

    worker._persist_dead_letter.assert_called_once()
    worker.client.xack.assert_called_once()


def test_stream_publish_uses_bounded_retention() -> None:
    redis = MagicMock()
    message = event()

    RedisStreamBus(redis).publish(message)

    redis.xadd.assert_called_once_with(
        message.stream.value,
        message.to_fields(),
        maxlen=10_000,
        approximate=True,
    )

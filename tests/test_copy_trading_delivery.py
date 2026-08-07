from unittest.mock import MagicMock
from threading import Event, Lock
import uuid

from app.domains.copy_trading.delivery import DeliveryResult, KeyedSerialExecutor
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


def test_retry_exhaustion_notifies_the_handler_before_dead_lettering() -> None:
    worker = worker_for(DeliveryResult.retry("PROVISIONING_ACCEPTED", "still running"))
    worker.retry_exhausted_handler = MagicMock()
    worker._pending_attempts.return_value = worker.max_attempts
    message = event()

    worker._process_message("1-0", message.to_fields())

    worker.retry_exhausted_handler.assert_called_once()
    exhausted_event, exhausted_result = worker.retry_exhausted_handler.call_args.args
    assert exhausted_event.event_id == message.event_id
    assert exhausted_result.error_code == "PROVISIONING_ACCEPTED"


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


def test_keyed_executor_serializes_one_source_but_runs_other_sources_concurrently() -> None:
    executor = KeyedSerialExecutor(max_workers=3)
    first_started = Event()
    release_first = Event()
    other_finished = Event()
    values = []
    values_lock = Lock()

    def first():
        first_started.set()
        release_first.wait(2)
        with values_lock:
            values.append("source-a-1")

    def second():
        with values_lock:
            values.append("source-a-2")

    def other():
        with values_lock:
            values.append("source-b")
        other_finished.set()

    executor.submit("source-a", first)
    assert first_started.wait(1)
    executor.submit("source-a", second)
    executor.submit("source-b", other)

    assert other_finished.wait(1)
    assert values == ["source-b"]
    release_first.set()
    executor.shutdown(wait=True)
    assert values == ["source-b", "source-a-1", "source-a-2"]

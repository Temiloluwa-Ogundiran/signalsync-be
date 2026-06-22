from dataclasses import dataclass
from enum import Enum
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable


class DeliveryDisposition(str, Enum):
    success = "success"
    retry = "retry"
    dead_letter = "dead_letter"


@dataclass(frozen=True)
class DeliveryResult:
    disposition: DeliveryDisposition
    error_code: str | None = None
    message: str | None = None

    @classmethod
    def success(cls) -> "DeliveryResult":
        return cls(DeliveryDisposition.success)

    @classmethod
    def retry(cls, error_code: str, message: str) -> "DeliveryResult":
        return cls(DeliveryDisposition.retry, error_code, message)

    @classmethod
    def dead_letter(cls, error_code: str, message: str) -> "DeliveryResult":
        return cls(DeliveryDisposition.dead_letter, error_code, message)


def normalize_delivery_result(result: DeliveryResult | None) -> DeliveryResult:
    # Compatibility for handlers migrated incrementally. New handlers should
    # always return a DeliveryResult explicitly.
    return result if isinstance(result, DeliveryResult) else DeliveryResult.success()


class KeyedSerialExecutor:
    """Run different keys concurrently while preserving order within one key."""

    def __init__(self, *, max_workers: int = 8):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="copy-worker")
        self._lock = Lock()
        self._queues: dict[str, deque] = {}

    def submit(self, key: str, function: Callable, *args, **kwargs) -> Future:
        result = Future()
        with self._lock:
            queue = self._queues.setdefault(key, deque())
            queue.append((result, function, args, kwargs))
            if len(queue) == 1:
                self._executor.submit(self._drain, key)
        return result

    def _drain(self, key: str) -> None:
        while True:
            with self._lock:
                queue = self._queues.get(key)
                if not queue:
                    self._queues.pop(key, None)
                    return
                result, function, args, kwargs = queue[0]
            try:
                result.set_result(function(*args, **kwargs))
            except BaseException as exc:
                result.set_exception(exc)
            with self._lock:
                queue = self._queues.get(key)
                if queue:
                    queue.popleft()
                    if not queue:
                        self._queues.pop(key, None)
                        return

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

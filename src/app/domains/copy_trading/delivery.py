from dataclasses import dataclass
from enum import Enum


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

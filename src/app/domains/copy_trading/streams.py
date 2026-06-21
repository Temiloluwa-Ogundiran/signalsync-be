import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class StreamName(str, Enum):
    telegram_commands = "copy:telegram:commands"
    telegram_messages = "copy:telegram:messages"
    signal_actions = "copy:signal:actions"
    execution_intents = "copy:execution:intents"
    learning_jobs = "copy:learning:jobs"
    outcomes = "copy:outcomes"
    dead_letters = "copy:dead-letters"


@dataclass(frozen=True)
class CopyEvent:
    event_id: str
    stream: StreamName
    event_type: str
    correlation_id: str
    idempotency_key: str
    occurred_at: str
    payload: dict[str, Any]

    @classmethod
    def new(
        cls,
        *,
        stream: StreamName,
        event_type: str,
        correlation_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> "CopyEvent":
        return cls(
            event_id=str(uuid.uuid4()),
            stream=stream,
            event_type=event_type,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )

    def to_fields(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "stream": self.stream.value,
            "event_type": self.event_type,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.occurred_at,
            "payload": json.dumps(self.payload, separators=(",", ":"), sort_keys=True),
        }

    @classmethod
    def from_fields(cls, fields: Mapping[str | bytes, str | bytes]) -> "CopyEvent":
        normalized = {
            (key.decode() if isinstance(key, bytes) else key):
            (value.decode() if isinstance(value, bytes) else value)
            for key, value in fields.items()
        }
        return cls(
            event_id=normalized["event_id"],
            stream=StreamName(normalized["stream"]),
            event_type=normalized["event_type"],
            correlation_id=normalized["correlation_id"],
            idempotency_key=normalized["idempotency_key"],
            occurred_at=normalized["occurred_at"],
            payload=json.loads(normalized["payload"]),
        )


class RedisStreamBus:
    def __init__(self, redis_client):
        self.redis = redis_client

    def publish(self, event: CopyEvent) -> str:
        return self.redis.xadd(event.stream.value, event.to_fields())

    def ensure_group(self, stream: StreamName, group: str) -> None:
        try:
            self.redis.xgroup_create(stream.value, group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

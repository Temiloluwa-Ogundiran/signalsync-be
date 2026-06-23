import argparse
import base64
from datetime import datetime, timezone

import redis

from app.core.config import settings
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--chat-id", required=True, type=int)
    parser.add_argument("--message-id", required=True, type=int)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--text-base64", required=True)
    args = parser.parse_args()
    text = base64.b64decode(args.text_base64).decode("utf-8")
    event = CopyEvent.new(
        stream=StreamName.telegram_messages,
        event_type="message.created",
        correlation_id=args.correlation_id,
        payload={
            "connection_id": args.connection_id,
            "source_id": args.source_id,
            "chat_id": args.chat_id,
            "message_id": args.message_id,
            "reply_to_message_id": None,
            "sender_id": 1,
            "sender_is_admin": True,
            "text": text,
            "is_forward": False,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        idempotency_key=f"synthetic:{args.correlation_id}",
    )
    client = redis.Redis.from_url(
        settings.COPY_TRADING_REDIS_URL,
        decode_responses=True,
    )
    message_id = RedisStreamBus(client).publish(event)
    print(f"published={message_id} correlation_id={args.correlation_id}")


if __name__ == "__main__":
    main()

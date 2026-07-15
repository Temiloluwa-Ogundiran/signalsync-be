import json
import logging

import redis
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyDeadLetter,
    CopiedTrade,
    CopyRoute,
    CopyExecutionMetric,
    CopySignalReview,
    CopyTradingConnection,
    CopyTradingUserSettings,
    TelegramConnection,
    TelegramSource,
    TradeIntent,
)


logger = logging.getLogger("copy-trading.live-updates")
_redis = redis.Redis.from_url(settings.COPY_TRADING_REDIS_URL, decode_responses=True)
_USER_MODELS = (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyDeadLetter,
    CopiedTrade,
    CopyRoute,
    CopyExecutionMetric,
    CopySignalReview,
    CopyTradingConnection,
    CopyTradingUserSettings,
    TelegramConnection,
    TelegramSource,
    TradeIntent,
)


def _update_user_id(session, item):
    user_id = getattr(item, "user_id", None)
    if user_id is not None:
        return user_id
    if isinstance(item, CopiedTrade):
        route = session.get(CopyRoute, item.route_id)
        return route.user_id if route is not None else None
    return None


@event.listens_for(Session, "before_flush")
def _collect_copy_trading_updates(session, _flush_context, _instances) -> None:
    user_ids = session.info.setdefault("copy_live_user_ids", set())
    for item in {*session.new, *session.dirty, *session.deleted}:
        if isinstance(item, _USER_MODELS):
            user_id = _update_user_id(session, item)
            if user_id is not None:
                user_ids.add(str(user_id))


@event.listens_for(Session, "after_commit")
def _publish_copy_trading_updates(session) -> None:
    user_ids = session.info.pop("copy_live_user_ids", set())
    for user_id in user_ids:
        try:
            _redis.publish(
                f"copy:live:{user_id}",
                json.dumps({"type": "copy-trading.updated"}, separators=(",", ":")),
            )
        except Exception:
            logger.warning(
                "Could not publish copy-trading live update user_id=%s",
                user_id,
                exc_info=True,
            )


@event.listens_for(Session, "after_rollback")
def _discard_copy_trading_updates(session) -> None:
    session.info.pop("copy_live_user_ids", None)

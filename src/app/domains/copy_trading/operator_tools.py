import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select

from app.core.config import settings
from app.domains.copy_trading.engine import ParsedSignal, RouteExecutionPolicy, SignalAction, validate_signal
from app.domains.copy_trading.execution import signal_volume_for_action
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyExecutionMetric,
    CopySignalReview,
    SignalConversation,
    SignalReviewState,
    SymbolMapping,
)
from app.domains.copy_trading.security import SessionCipher
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName
from app.domains.copy_trading.symbols import normalize_symbol
from app.domains.copy_trading.telemetry import percentile
from app.domains.copy_trading.workers import _parse_message


def preview_route(db, *, current_user, route, text: str, occurred_at: datetime | None) -> dict:
    policy = db.execute(
        select(CopyAccountPolicy).where(
            CopyAccountPolicy.connection_id == route.target_connection_id,
            CopyAccountPolicy.user_id == current_user.id,
        )
    ).scalar_one()
    parsed = _parse_message(text, {})
    now = datetime.now(timezone.utc)
    source_time = occurred_at or now
    if source_time.tzinfo is None:
        source_time = source_time.replace(tzinfo=timezone.utc)
    signal = ParsedSignal(
        action=SignalAction(parsed.action.value),
        symbol=parsed.symbol,
        direction=parsed.direction,
        entry=parsed.entry,
        entry_high=parsed.entry_high,
        stop_loss=parsed.stop_loss,
        take_profits=parsed.take_profits,
        close_fraction=parsed.close_fraction,
        age_seconds=max(0, (now - source_time).total_seconds()),
        confidence=float(parsed.confidence),
    )
    validation = validate_signal(
        signal,
        RouteExecutionPolicy(
            minimum_fields=route.minimum_fields.value,
            confidence_threshold=settings.COPY_TRADING_CONFIDENCE_THRESHOLD,
            market_freshness_seconds=policy.market_signal_max_age_seconds,
            pending_orders_enabled=route.pending_orders_enabled,
            allow_sl_tp_updates=route.allow_sl_tp_updates,
            allow_break_even=route.allow_break_even,
            allow_partial_close=route.allow_partial_close,
            allow_full_close=route.allow_full_close,
            allow_pending_cancel=route.allow_pending_cancel,
            allow_additional_tp=route.allow_additional_tp,
        ),
    )
    broker_symbol = None
    if parsed.symbol:
        mapping = db.execute(
            select(SymbolMapping).where(
                SymbolMapping.route_id == route.id,
                SymbolMapping.normalized_signal_symbol == normalize_symbol(parsed.symbol),
            )
        ).scalar_one_or_none()
        broker_symbol = mapping.broker_symbol if mapping else parsed.symbol
    volume = signal_volume_for_action(
        action=signal.action,
        fixed_lot=route.fixed_lot,
        take_profit_count=len(signal.take_profits),
        take_profit_mode=route.take_profit_mode.value,
        distribution=route.lot_distribution.value,
    )
    warnings = [item for item in [validation.advisory] if item]
    if parsed.symbol and broker_symbol == parsed.symbol:
        warnings.append("Broker symbol will be confirmed from the live catalog before execution.")
    return {
        "accepted": validation.accepted,
        "reason": validation.reason,
        "action": signal.action.value,
        "signal_symbol": parsed.symbol,
        "broker_symbol": broker_symbol,
        "direction": parsed.direction,
        "volume": str(volume) if volume > 0 else None,
        "take_profits": [str(value) for value in parsed.take_profits],
        "warnings": warnings,
    }


def latency_summary(db, *, user_id: uuid.UUID) -> dict:
    rows = list(
        db.execute(
            select(CopyExecutionMetric)
            .where(CopyExecutionMetric.user_id == user_id)
            .order_by(CopyExecutionMetric.created_at.desc())
            .limit(500)
        ).scalars()
    )
    totals = [row.total_ms for row in rows if row.total_ms is not None]
    return {
        "sample_count": len(totals),
        "p50_ms": percentile(totals, 0.50),
        "p95_ms": percentile(totals, 0.95),
        "p99_ms": percentile(totals, 0.99),
        "target_ms": 2000,
        "over_target_count": sum(value > 2000 for value in totals),
        "recent": [
            {
                "correlation_id": row.correlation_id,
                "action": row.action,
                "symbol": row.symbol,
                "status": row.status,
                "ingestion_ms": row.ingestion_ms,
                "assembly_ms": row.assembly_ms,
                "broker_ms": row.broker_ms,
                "total_ms": row.total_ms,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows[:20]
        ],
    }


def list_reviews(db, *, user_id: uuid.UUID) -> list[CopySignalReview]:
    return list(
        db.execute(
            select(CopySignalReview)
            .where(
                CopySignalReview.user_id == user_id,
                CopySignalReview.state == SignalReviewState.pending,
            )
            .order_by(CopySignalReview.created_at.desc())
        ).scalars()
    )


def resolve_review(db, *, user_id: uuid.UUID, review_id: uuid.UUID, conversation_id: uuid.UUID | None, client) -> CopySignalReview:
    review = db.execute(
        select(CopySignalReview).where(
            CopySignalReview.id == review_id,
            CopySignalReview.user_id == user_id,
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signal review not found.")
    if review.state != SignalReviewState.pending:
        return review
    if conversation_id is None:
        review.state = SignalReviewState.ignored
        review.resolved_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(review)
        return review
    allowed = {item.get("conversation_id") for item in review.candidates}
    if str(conversation_id) not in allowed:
        raise HTTPException(status_code=422, detail="Choose one of the suggested signal conversations.")
    conversation = db.get(SignalConversation, conversation_id)
    if conversation is None or conversation.source_id != review.source_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The selected signal is no longer available.")
    payload = json.loads(SessionCipher(settings.ENCRYPTION_KEY).decrypt(review.encrypted_event_payload))
    payload["reply_to_message_id"] = conversation.last_message_id
    event = CopyEvent.new(
        stream=StreamName.telegram_messages,
        event_type="message.created",
        correlation_id=f"review:{review.id}",
        payload=payload,
        idempotency_key=f"review:{review.id}",
    )
    RedisStreamBus(client).publish(event)
    review.state = SignalReviewState.approved
    review.resolved_conversation_id = conversation.id
    review.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(review)
    return review

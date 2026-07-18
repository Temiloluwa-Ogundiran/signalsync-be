import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from redis.exceptions import LockNotOwnedError
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.delivery import DeliveryResult
from app.domains.copy_trading.engine import SignalAction
from app.domains.copy_trading.execution import ensure_account_risk_within_limits
from app.domains.copy_trading.metaapi_broker import MetaApiBroker
from app.domains.copy_trading.metaapi_connections import get_metaapi_runtime
from app.domains.copy_trading.quality import ExecutionQualityError, check_execution_quality
from app.domains.copy_trading.telemetry import record_execution_metric
from app.domains.notifications.models import NotificationType
from app.domains.notifications.service import notify
from app.domains.users.models import User
from app.domains.copy_trading.reconciliation import apply_broker_snapshot
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyActivityLevel,
    CopiedTrade,
    CopyRoute,
    CopyRouteState,
    CopyTradingConnection,
    CopyTradingConnectionState,
    CopyTradingUserSettings,
    ParsedAction,
    SymbolMapping,
    TradeIntent,
    TradeIntentState,
)
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName
from app.domains.copy_trading.symbols import normalize_symbol, normalize_volume, resolve_symbol


OPEN_ACTIONS = {
    SignalAction.open_market.value,
    SignalAction.place_pending.value,
    SignalAction.additional_tp.value,
}

logger = logging.getLogger("copy-trading.metaapi-execution")


def _connection_lock(client, connection_id):
    # Acquisition and the broker action can each consume one MetaApi timeout.
    lease_seconds = max(300, settings.METAAPI_CONNECTION_TIMEOUT_SECONDS * 2 + 60)
    return client.lock(
        f"copy:connection-lock:{connection_id}",
        timeout=lease_seconds,
        blocking_timeout=10,
    )


def _release_connection_lock(lock, *, connection_id) -> None:
    try:
        lock.release()
    except LockNotOwnedError:
        logger.warning(
            "Connection lock expired before release connection_id=%s",
            connection_id,
        )


def warm_active_copy_connections() -> int:
    if not settings.COPY_TRADING_METAAPI_ENABLED:
        return 0
    with SessionLocal() as db:
        account_ids = list(
            db.execute(
                select(CopyTradingConnection.metaapi_account_id)
                .join(
                    CopyRoute,
                    CopyRoute.target_connection_id == CopyTradingConnection.id,
                )
                .where(
                    CopyRoute.state == CopyRouteState.active,
                    CopyTradingConnection.state == CopyTradingConnectionState.ready,
                    CopyTradingConnection.metaapi_account_id.is_not(None),
                )
                .distinct()
            ).scalars()
        )
    runtime = get_metaapi_runtime()
    for account_id in account_ids:
        runtime.acquire(account_id)
    return len(account_ids)


def is_permanent_metaapi_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return False
    name = exc.__class__.__name__.lower()
    if any(word in name for word in ("timeout", "connection", "socket", "rate")):
        return False
    return isinstance(exc, ValueError) or any(
        word in name for word in ("validation", "trade", "forbidden", "unauthorized")
    )


def _catalog_fingerprint(specifications: list[dict]) -> str:
    payload = json.dumps(specifications, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_broker_symbol(db, route, connection, broker, requested: str):
    specifications = list(broker.connection.terminal_state.specifications)
    version = _catalog_fingerprint(specifications)
    symbols = broker.symbols()
    normalized = normalize_symbol(requested)
    saved = db.execute(
        select(SymbolMapping).where(
            SymbolMapping.route_id == route.id,
            SymbolMapping.connection_id == connection.id,
            SymbolMapping.normalized_signal_symbol == normalized,
        )
    ).scalar_one_or_none()
    available = {symbol.name: symbol for symbol in symbols}
    if saved and saved.catalog_version == version and saved.broker_symbol in available:
        selected = available[saved.broker_symbol]
    else:
        selected = resolve_symbol(requested, symbols)
        evidence = {
            "contract_size": str(selected.contract_size),
            "min_volume": str(selected.min_volume),
            "max_volume": str(selected.max_volume),
            "volume_step": str(selected.volume_step),
        }
        if saved:
            saved.broker_symbol = selected.name
            saved.selection_evidence = evidence
            saved.catalog_version = version
        else:
            db.add(
                SymbolMapping(
                    route_id=route.id,
                    connection_id=connection.id,
                    normalized_signal_symbol=normalized,
                    broker_symbol=selected.name,
                    selection_evidence=evidence,
                    catalog_version=version,
                )
            )
    connection.symbol_catalog_fingerprint = version
    connection.symbol_catalog_refreshed_at = datetime.now(timezone.utc)
    return selected


def _select_trade(trades: list[CopiedTrade], payload: dict) -> CopiedTrade | None:
    copied_trade_id = payload.get("copied_trade_id")
    if copied_trade_id:
        return next(
            (trade for trade in trades if str(trade.id) == str(copied_trade_id)),
            None,
        )
    if payload.get("symbol"):
        target = normalize_symbol(str(payload["symbol"]))
        return next(
            (trade for trade in trades if normalize_symbol(trade.signal_symbol).startswith(target)),
            None,
        )
    return trades[0] if len(trades) == 1 else None


def _record_activity(
    db,
    route,
    event: CopyEvent,
    action: str,
    title: str,
    level,
    *,
    body: str | None = None,
    parsed_details: dict | None = None,
    broker_details: dict | None = None,
) -> None:
    db.add(
        CopyActivityEvent(
            user_id=route.user_id,
            route_id=route.id,
            source_id=route.source_id,
            connection_id=route.target_connection_id,
            correlation_id=event.correlation_id,
            action=action,
            title=title[:200],
            level=level,
            body=body,
            parsed_details=parsed_details or {},
            broker_details=broker_details or {},
        )
    )


def _notify_execution(db, *, route, title: str, body: str, success: bool, details: dict) -> None:
    enabled = route.notify_success if success else route.notify_failure
    if not enabled:
        return
    notify(
        db,
        user_id=route.user_id,
        title=title,
        body=body,
        link="/copy-trading/activity",
        type=NotificationType.success if success else NotificationType.error,
    )
    user = db.get(User, route.user_id)
    if user and user.email:
        try:
            from app.tasks.copy_trading_tasks import send_execution_email_task

            send_execution_email_task.apply_async(
                args=(user.email, title, details),
                ignore_result=True,
            )
        except Exception:
            logger.exception("Could not enqueue copy-trading email user_id=%s", route.user_id)


def _quality_details(policy, broker, selected, payload, runtime) -> dict:
    try:
        price = runtime.run(broker.ensure_price(selected.name))
    except Exception as exc:
        raise ExecutionQualityError(
            "QUOTE_UNAVAILABLE",
            "A live broker quote is not available yet.",
            retryable=True,
            user_action_required=False,
        ) from exc
    quality = check_execution_quality(
        price=price,
        symbol=selected,
        direction=str(payload.get("direction") or "buy"),
        entry=Decimal(str(payload["entry"])) if payload.get("entry") is not None else None,
        max_spread_points=policy.max_spread_points,
        max_slippage_points=policy.max_slippage_points,
        max_quote_age_seconds=policy.max_quote_age_seconds,
        high_spread_behavior=policy.high_spread_behavior,
        trading_start_hour_utc=policy.trading_start_hour_utc,
        trading_end_hour_utc=policy.trading_end_hour_utc,
    )
    return {
        "spread_points": str(quality.spread_points),
        "quote_age_seconds": round(quality.quote_age_seconds, 3),
        "slippage_points": str(quality.slippage_points) if quality.slippage_points is not None else None,
    }


def _activity_copy(payload: dict, result: dict) -> tuple[str, str]:
    symbol = str(payload.get("symbol") or "Trade")
    action = payload["action"]
    labels = {
        SignalAction.open_market.value: (
            f"{symbol} trade opened",
            "The market order was accepted by the trading account.",
        ),
        SignalAction.place_pending.value: (
            f"{symbol} pending order placed",
            "The pending order is waiting for its entry price.",
        ),
        SignalAction.modify_sl_tp.value: (
            f"{symbol} protection updated",
            "The stop loss and take profit were updated.",
        ),
        SignalAction.break_even.value: (
            f"{symbol} moved to break even",
            "The stop loss was moved to the entry price.",
        ),
        SignalAction.partial_close.value: (
            f"{symbol} position reduced",
            "Part of the copied position was closed.",
        ),
        SignalAction.full_close.value: (
            f"{symbol} position closed",
            "The copied position was closed.",
        ),
        SignalAction.cancel_pending.value: (
            f"{symbol} pending order cancelled",
            "The copied pending order was cancelled.",
        ),
        SignalAction.additional_tp.value: (
            f"{symbol} take-profit instruction copied",
            "The additional take-profit instruction was accepted.",
        ),
    }
    return labels.get(
        action,
        ("Copy action completed", "The trading account accepted the instruction."),
    )


def _friendly_broker_error(exc: Exception) -> str:
    message = str(exc).strip()
    lowered = message.lower()
    if "market is closed" in lowered:
        return "The market is closed for this symbol. No trade was placed."
    if "invalid stops" in lowered or "stop" in lowered and "invalid" in lowered:
        return "The broker rejected the stop loss or take profit. Check the price distance."
    if "not enough money" in lowered or "no money" in lowered or "margin" in lowered:
        return "The account does not have enough free margin for this trade."
    if "volume" in lowered:
        return "The broker rejected the trade size for this symbol."
    if "timeout" in lowered or "temporar" in lowered or "connection" in lowered:
        return "The broker did not confirm the request yet. TradePartna is checking its status."
    return message[:500] or "The broker rejected the instruction."


def _publish_reconcile(client, event: CopyEvent, intent: TradeIntent) -> None:
    RedisStreamBus(client).publish(
        CopyEvent.new(
            stream=StreamName.execution_intents,
            event_type="intent.reconcile",
            correlation_id=event.correlation_id,
            payload={"intent_id": str(intent.id), "connection_id": str(intent.connection_id)},
            idempotency_key=f"reconcile:{intent.id}:{intent.attempt_count}",
        )
    )


def _uncertain_intent_expired(intent: TradeIntent, now: datetime) -> bool:
    age_seconds = max(0, (now - intent.created_at).total_seconds())
    return age_seconds >= settings.COPY_TRADING_UNCERTAIN_MAX_AGE_SECONDS


def _reconcile_sweep_bucket(now: datetime) -> int:
    bucket_seconds = max(
        1, min(30, settings.COPY_TRADING_UNCERTAIN_MAX_AGE_SECONDS)
    )
    return int(now.timestamp()) // bucket_seconds


def _trade_options(route, intent) -> dict:
    return {
        "clientId": intent.client_order_id,
        "magic": route.magic_number,
    }


def _mark_trade_terminal(copied, action: str) -> None:
    copied.lifecycle_state = (
        "closed" if action == SignalAction.full_close.value else "cancelled"
    )
    copied.current_volume = Decimal("0")


def _enforce_live_account_risk(db, *, policy, route, broker, payload, signal_volume) -> None:
    if signal_volume is None or signal_volume <= 0:
        return
    account_information = broker.account_information()
    equity_value = account_information.get("equity")
    equity = Decimal(str(equity_value)) if equity_value is not None else None
    today = datetime.now(timezone.utc).date()
    if equity is not None:
        if policy.daily_equity_anchor_date != today:
            policy.daily_equity_anchor_date = today
            policy.daily_equity_anchor = equity
        if policy.peak_equity is None or equity > policy.peak_equity:
            policy.peak_equity = equity

    active_trades = list(
        db.execute(
            select(CopiedTrade)
            .join(CopyRoute, CopyRoute.id == CopiedTrade.route_id)
            .where(
                CopyRoute.target_connection_id == route.target_connection_id,
                CopiedTrade.lifecycle_state.in_(["open", "pending"]),
            )
        ).scalars()
    )
    current_exposure = sum(
        (
            Decimal(str(trade.current_volume or trade.original_volume or 0))
            for trade in active_trades
        ),
        Decimal("0"),
    )
    ensure_account_risk_within_limits(
        symbol=str(payload.get("symbol") or ""),
        signal_volume=Decimal(str(signal_volume)),
        current_exposure=current_exposure,
        current_positions=len(active_trades),
        equity=equity,
        daily_equity_anchor=policy.daily_equity_anchor,
        peak_equity=policy.peak_equity,
        max_lot_per_trade=policy.max_lot_per_trade,
        max_total_lot=policy.max_lot,
        max_open_positions=policy.max_open_positions,
        daily_loss_limit=policy.daily_loss_limit,
        max_drawdown_percent=policy.max_drawdown_percent,
        allowed_symbols=policy.allowed_symbols,
        blocked_symbols=policy.blocked_symbols,
    )


def _run_action(runtime, broker, route, intent, payload, copied, selected):
    action = payload["action"]
    options = _trade_options(route, intent)
    if action in {SignalAction.open_market.value, SignalAction.additional_tp.value}:
        volume = normalize_volume(Decimal(str(payload.get("volume", route.fixed_lot))), selected)
        return runtime.run(
            broker.market_order(
                direction=payload["direction"],
                symbol=selected.name,
                volume=float(volume),
                stop_loss=payload.get("stop_loss"),
                take_profit=payload.get("take_profit"),
                options=options,
            )
        ), volume
    if action == SignalAction.place_pending.value:
        volume = normalize_volume(Decimal(str(payload.get("volume", route.fixed_lot))), selected)
        order_type = str(payload.get("order_type") or "limit").lower().split("_")[-1]
        return runtime.run(
            broker.pending_order(
                direction=payload["direction"],
                order_type=order_type,
                symbol=selected.name,
                volume=float(volume),
                open_price=float(payload["entry"]),
                stop_loss=payload.get("stop_loss"),
                take_profit=payload.get("take_profit"),
                options=options,
            )
        ), volume
    if copied is None:
        raise ValueError(f"Management action {action} requires a matched copied trade.")
    if action in {SignalAction.modify_sl_tp.value, SignalAction.break_even.value}:
        return runtime.run(
            broker.modify_position(
                copied.broker_position_id,
                stop_loss=payload.get("stop_loss") or payload.get("entry"),
                take_profit=payload.get("take_profit"),
            )
        ), None
    if action == SignalAction.partial_close.value:
        current = Decimal(str(copied.current_volume or copied.original_volume))
        volume = current * Decimal(str(payload.get("close_fraction") or "0.5"))
        return runtime.run(
            broker.close_position(copied.broker_position_id, volume=float(volume), options=options)
        ), volume
    if action == SignalAction.full_close.value:
        return runtime.run(broker.close_position(copied.broker_position_id, options=options)), None
    if action == SignalAction.cancel_pending.value:
        return runtime.run(broker.cancel_order(copied.broker_order_id)), None
    raise ValueError(f"Unsupported copy action {action}.")


def execution_handler(event: CopyEvent, client) -> DeliveryResult:
    if event.event_type == "emergency.execute":
        return execute_emergency(event, client)
    if event.event_type == "intent.reconcile":
        return reconcile_intent(event, client)
    if event.event_type != "intent.execute":
        return DeliveryResult.success()
    intent_id = uuid.UUID(event.payload["intent_id"])
    with SessionLocal() as db:
        intent = db.get(TradeIntent, intent_id)
        if intent is None:
            return DeliveryResult.retry("INTENT_NOT_VISIBLE", "Trade intent is not committed yet.")
        if intent.state not in {TradeIntentState.created, TradeIntentState.retryable}:
            return DeliveryResult.success()
        if settings.BILLING_ENFORCED:
            from app.domains.billing import service as billing_service
            from app.domains.billing.entitlements import has_copy_access

            subscription = billing_service.get_subscription(db, user_id=intent.user_id)
            if not has_copy_access(
                billing_service.effective_subscription(subscription) if subscription else None
            ) and intent.request_payload.get("action") in OPEN_ACTIONS:
                intent.state = TradeIntentState.failed
                intent.last_error_code = "SUBSCRIPTION_INACTIVE"
                db.commit()
                return DeliveryResult.success()
        route = db.get(CopyRoute, intent.route_id)
        connection = db.get(CopyTradingConnection, intent.connection_id)
        parsed_action = db.get(ParsedAction, intent.parsed_action_id)
        lock = _connection_lock(client, intent.connection_id)
        if not lock.acquire(blocking=True):
            return DeliveryResult.retry("CONNECTION_BUSY", "The copy account is processing another action.")
        try:
            user_settings = db.get(CopyTradingUserSettings, intent.user_id)
            policy = db.execute(
                select(CopyAccountPolicy).where(CopyAccountPolicy.connection_id == intent.connection_id)
            ).scalar_one_or_none()
            if (
                settings.COPY_TRADING_GLOBAL_PAUSED
                or not settings.COPY_TRADING_METAAPI_ENABLED
                or route is None
                or route.state != CopyRouteState.active
                or connection is None
                or connection.state != CopyTradingConnectionState.ready
                or not connection.metaapi_account_id
                or (user_settings and user_settings.is_paused)
                or (policy and policy.is_paused)
            ):
                intent.state = TradeIntentState.failed
                intent.last_error_code = "AUTOMATION_PAUSED"
                db.commit()
                return DeliveryResult.success()
            runtime = get_metaapi_runtime()
            try:
                broker = MetaApiBroker(runtime.acquire(connection.metaapi_account_id))
            except Exception as exc:
                runtime.mark_unhealthy(connection.metaapi_account_id)
                return DeliveryResult.retry(exc.__class__.__name__.upper(), str(exc))
            payload = intent.request_payload
            selected = (
                _resolve_broker_symbol(db, route, connection, broker, payload["symbol"])
                if payload.get("symbol")
                else None
            )
            copied_trades = list(
                db.execute(
                    select(CopiedTrade)
                    .where(
                        CopiedTrade.route_id == route.id,
                        CopiedTrade.lifecycle_state.in_(["open", "pending"]),
                    )
                    .order_by(CopiedTrade.created_at.desc())
                ).scalars()
            )
            copied = _select_trade(copied_trades, payload)
            requested_volume = (
                Decimal(str(payload.get("volume", route.fixed_lot)))
                if payload["action"] in OPEN_ACTIONS
                else None
            )
            if payload["action"] in OPEN_ACTIONS and selected is not None:
                try:
                    payload["_execution_quality"] = _quality_details(
                        policy, broker, selected, payload, runtime
                    )
                except ExecutionQualityError as exc:
                    intent.attempt_count += 1
                    if exc.retryable and intent.attempt_count <= 1:
                        intent.state = TradeIntentState.retryable
                        intent.last_error_code = exc.code
                        db.commit()
                        return DeliveryResult.retry(exc.code, str(exc))
                    if not exc.user_action_required:
                        intent.state = TradeIntentState.failed
                        intent.last_error_code = exc.code
                        intent.broker_result = {"message": str(exc)}
                        record_execution_metric(db, intent=intent, route=route, status="failed")
                        logger.warning(
                            "Copy execution stopped after quote recovery failed intent_id=%s code=%s",
                            intent.id,
                            exc.code,
                        )
                        db.commit()
                        return DeliveryResult.success()
                    intent.state = TradeIntentState.failed
                    intent.last_error_code = exc.code
                    intent.broker_result = {"message": str(exc)}
                    _record_activity(
                        db,
                        route,
                        event,
                        "execution.blocked",
                        "Trade blocked by execution settings",
                        CopyActivityLevel.warning,
                        body=str(exc),
                        parsed_details=payload,
                        broker_details={"error_code": exc.code},
                    )
                    record_execution_metric(db, intent=intent, route=route, status="blocked")
                    _notify_execution(
                        db,
                        route=route,
                        title="Copied trade blocked",
                        body=str(exc),
                        success=False,
                        details={"symbol": payload.get("symbol"), "reason": str(exc)},
                    )
                    db.commit()
                    return DeliveryResult.success()
            try:
                _enforce_live_account_risk(
                    db,
                    policy=policy,
                    route=route,
                    broker=broker,
                    payload=payload,
                    signal_volume=requested_volume,
                )
            except ValueError as exc:
                intent.state = TradeIntentState.failed
                intent.last_error_code = "RISK_LIMIT_REACHED"
                intent.broker_result = {"message": str(exc)}
                _record_activity(
                    db,
                    route,
                    event,
                    "risk.blocked",
                    "Trade blocked by account safety settings",
                    CopyActivityLevel.warning,
                    body=str(exc),
                    parsed_details=payload,
                    broker_details={"error_code": "RISK_LIMIT_REACHED"},
                )
                record_execution_metric(db, intent=intent, route=route, status="blocked")
                _notify_execution(
                    db,
                    route=route,
                    title="Copied trade blocked",
                    body=str(exc),
                    success=False,
                    details={"symbol": payload.get("symbol"), "reason": str(exc)},
                )
                db.commit()
                return DeliveryResult.success()
            intent.state = TradeIntentState.submitted
            intent.submitted_at = datetime.now(timezone.utc)
            payload.setdefault("_telemetry", {})["submitted_at"] = intent.submitted_at.isoformat()
            intent.attempt_count += 1
            db.commit()
            try:
                result, submitted_volume = _run_action(
                    runtime, broker, route, intent, payload, copied, selected
                )
            except Exception as exc:
                resolved_at = datetime.now(timezone.utc)
                payload.setdefault("_telemetry", {})["resolved_at"] = resolved_at.isoformat()
                permanent = is_permanent_metaapi_error(exc)
                intent.state = TradeIntentState.failed if permanent else TradeIntentState.uncertain
                intent.last_error_code = exc.__class__.__name__
                intent.broker_result = {"message": str(exc)[:500]}
                _record_activity(
                    db,
                    route,
                    event,
                    "broker.failed" if permanent else "broker.uncertain",
                    "Copy trade rejected" if permanent else "Confirming broker status",
                    CopyActivityLevel.error if permanent else CopyActivityLevel.warning,
                    body=_friendly_broker_error(exc),
                    parsed_details=payload,
                    broker_details={
                        "error_code": exc.__class__.__name__,
                        "message": str(exc)[:500],
                    },
                )
                record_execution_metric(
                    db,
                    intent=intent,
                    route=route,
                    status="failed" if permanent else "uncertain",
                )
                _notify_execution(
                    db,
                    route=route,
                    title="Copy trade rejected" if permanent else "Broker confirmation delayed",
                    body=_friendly_broker_error(exc),
                    success=False,
                    details={"symbol": payload.get("symbol"), "reason": str(exc)[:500]},
                )
                db.commit()
                if not permanent:
                    runtime.mark_unhealthy(connection.metaapi_account_id)
                    _publish_reconcile(client, event, intent)
                return DeliveryResult.success()

            intent.state = TradeIntentState.confirmed
            intent.broker_result = result
            intent.resolved_at = datetime.now(timezone.utc)
            payload.setdefault("_telemetry", {})["resolved_at"] = intent.resolved_at.isoformat()
            action = payload["action"]
            if action in OPEN_ACTIONS:
                db.add(
                    CopiedTrade(
                        route_id=route.id,
                        thread_id=parsed_action.thread_id,
                        intent_id=intent.id,
                        connection_id=connection.id,
                        magic_number=route.magic_number,
                        route_comment=f"cp:{str(route.id)[:8]}",
                        signal_symbol=str(payload.get("symbol")),
                        broker_symbol=selected.name,
                        broker_order_id=result.get("order_id"),
                        broker_deal_id=result.get("deal_id"),
                        broker_position_id=result.get("position_id") or result.get("order_id"),
                        lifecycle_state="pending" if action == SignalAction.place_pending.value else "open",
                        original_volume=submitted_volume,
                        current_volume=submitted_volume,
                        stop_loss=Decimal(str(payload["stop_loss"])) if payload.get("stop_loss") is not None else None,
                        take_profit=Decimal(str(payload["take_profit"])) if payload.get("take_profit") is not None else None,
                        broker_synced_at=datetime.now(timezone.utc),
                    )
                )
            elif copied and action == SignalAction.partial_close.value:
                copied.current_volume = max(Decimal("0"), Decimal(copied.current_volume) - submitted_volume)
            elif copied and action in {SignalAction.modify_sl_tp.value, SignalAction.break_even.value}:
                if payload.get("stop_loss") is not None or payload.get("entry") is not None:
                    copied.stop_loss = Decimal(str(payload.get("stop_loss") or payload.get("entry")))
                if payload.get("take_profit") is not None:
                    copied.take_profit = Decimal(str(payload["take_profit"]))
            elif copied and action in {SignalAction.full_close.value, SignalAction.cancel_pending.value}:
                _mark_trade_terminal(copied, action)
            if copied:
                copied.broker_synced_at = datetime.now(timezone.utc)
            activity_title, activity_body = _activity_copy(payload, result)
            _record_activity(
                db,
                route,
                event,
                "broker.confirmed",
                activity_title,
                CopyActivityLevel.success,
                body=activity_body,
                parsed_details=payload,
                broker_details=result,
            )
            metric = record_execution_metric(db, intent=intent, route=route, status="confirmed")
            if metric.total_ms is not None and metric.total_ms > 2000:
                _record_activity(
                    db,
                    route,
                    event,
                    "latency.slow",
                    "Copy execution exceeded the 2-second target",
                    CopyActivityLevel.warning,
                    body=f"End-to-end execution took {metric.total_ms / 1000:.2f} seconds.",
                    parsed_details={"total_ms": metric.total_ms},
                )
            _notify_execution(
                db,
                route=route,
                title=activity_title,
                body=activity_body,
                success=True,
                details={
                    "symbol": payload.get("symbol"),
                    "action": payload.get("action"),
                    "volume": str(submitted_volume) if submitted_volume is not None else None,
                    **result,
                },
            )
            db.commit()
            return DeliveryResult.success()
        finally:
            _release_connection_lock(lock, connection_id=intent.connection_id)


def execute_emergency(event: CopyEvent, client) -> DeliveryResult:
    payload = event.payload
    with SessionLocal() as db:
        query = (
            select(CopiedTrade, CopyRoute, CopyTradingConnection)
            .join(CopyRoute, CopyRoute.id == CopiedTrade.route_id)
            .join(
                CopyTradingConnection,
                CopyTradingConnection.id == CopyRoute.target_connection_id,
            )
            .where(
                CopyRoute.user_id == uuid.UUID(payload["user_id"]),
                CopiedTrade.lifecycle_state.in_(["open", "pending"]),
            )
        )
        scope = payload.get("scope")
        scope_id = payload.get("scope_id")
        if scope == "account":
            query = query.where(CopyRoute.target_connection_id == uuid.UUID(scope_id))
        elif scope == "source":
            query = query.where(CopyRoute.source_id == uuid.UUID(scope_id))
        elif scope == "route":
            query = query.where(CopyRoute.id == uuid.UUID(scope_id))
        rows = db.execute(query).all()
        runtime = get_metaapi_runtime()
        for trade, route, connection in rows:
            if not connection.metaapi_account_id:
                continue
            lock = _connection_lock(client, connection.id)
            if not lock.acquire(blocking=True):
                return DeliveryResult.retry(
                    "CONNECTION_BUSY", "A copy account is processing another action."
                )
            try:
                broker = MetaApiBroker(runtime.acquire(connection.metaapi_account_id))
                action = payload["action"]
                if action in {"close_positions", "both"} and trade.lifecycle_state == "open":
                    runtime.run(
                        broker.close_position(
                            trade.broker_position_id,
                            options={
                                "magic": route.magic_number,
                            },
                        )
                    )
                    trade.lifecycle_state = "closed"
                if action in {"cancel_pending", "both"} and trade.lifecycle_state == "pending":
                    runtime.run(broker.cancel_order(trade.broker_order_id))
                    trade.lifecycle_state = "cancelled"
                trade.broker_synced_at = datetime.now(timezone.utc)
                db.commit()
            finally:
                _release_connection_lock(lock, connection_id=connection.id)
        return DeliveryResult.success()


def reconcile_intent(event: CopyEvent, _client) -> DeliveryResult:
    intent_id = uuid.UUID(event.payload["intent_id"])
    with SessionLocal() as db:
        intent = db.get(TradeIntent, intent_id)
        if intent is None or intent.state not in {TradeIntentState.uncertain, TradeIntentState.reconciling}:
            return DeliveryResult.success()
        connection = db.get(CopyTradingConnection, intent.connection_id)
        if connection is None or not connection.metaapi_account_id:
            return DeliveryResult.retry("CONNECTION_UNAVAILABLE", "Copy account is unavailable.")
        intent.state = TradeIntentState.reconciling
        db.commit()
        runtime = get_metaapi_runtime()
        broker = MetaApiBroker(runtime.acquire(connection.metaapi_account_id))
        order = broker.find_order(client_id=intent.client_order_id)
        position = broker.find_position(client_id=intent.client_order_id)
        deal = broker.find_deal(client_id=intent.client_order_id)
        if order or position or deal:
            intent.state = TradeIntentState.confirmed
            intent.broker_result = {"order": order, "position": position, "deal": deal}
            intent.resolved_at = datetime.now(timezone.utc)
        else:
            now = datetime.now(timezone.utc)
            if _uncertain_intent_expired(intent, now):
                intent.state = TradeIntentState.failed
                intent.last_error_code = "BROKER_CONFIRMATION_TIMEOUT"
                intent.resolved_at = now
            else:
                intent.state = TradeIntentState.uncertain
        db.commit()
        return DeliveryResult.success()


def reconcile_copied_trades() -> int:
    updated = 0
    with SessionLocal() as db:
        rows = db.execute(
            select(CopiedTrade, CopyTradingConnection, TradeIntent, CopyRoute)
            .join(CopyRoute, CopyRoute.id == CopiedTrade.route_id)
            .join(CopyTradingConnection, CopyTradingConnection.id == CopyRoute.target_connection_id)
            .join(TradeIntent, TradeIntent.id == CopiedTrade.intent_id)
            .where(CopiedTrade.lifecycle_state.in_(["open", "pending"]))
        ).all()
        brokers = {}
        runtime = get_metaapi_runtime()
        for trade, connection, source_intent, route in rows:
            if not connection.metaapi_account_id:
                continue
            broker = brokers.setdefault(
                connection.id,
                MetaApiBroker(runtime.acquire(connection.metaapi_account_id)),
            )
            positions = broker.positions()
            orders = broker.orders()
            before = {
                "lifecycle_state": trade.lifecycle_state,
                "current_volume": str(trade.current_volume),
                "stop_loss": str(trade.stop_loss),
                "take_profit": str(trade.take_profit),
            }
            if apply_broker_snapshot(
                trade,
                positions=positions,
                orders=orders,
                observed_at=datetime.now(timezone.utc),
                client_order_id=getattr(source_intent, "client_order_id", None),
            ):
                updated += 1
                after = {
                    "lifecycle_state": trade.lifecycle_state,
                    "current_volume": str(trade.current_volume),
                    "stop_loss": str(trade.stop_loss),
                    "take_profit": str(trade.take_profit),
                }
                if before["lifecycle_state"] != after["lifecycle_state"]:
                    title = (
                        f"{trade.broker_symbol} position closed outside TradePartna"
                        if after["lifecycle_state"] == "closed"
                        else f"{trade.broker_symbol} broker status changed"
                    )
                    body = (
                        "The local copy record was updated to match the trading account."
                    )
                    level = CopyActivityLevel.warning
                else:
                    title = f"{trade.broker_symbol} broker changes synchronized"
                    body = (
                        "Trade size, stop loss, or take profit changed directly at the broker. "
                        "TradePartna updated its local record."
                    )
                    level = CopyActivityLevel.info
                db.add(
                    CopyActivityEvent(
                        user_id=route.user_id,
                        route_id=route.id,
                        source_id=route.source_id,
                        connection_id=route.target_connection_id,
                        correlation_id=f"reconcile:{trade.id}:{int(datetime.now(timezone.utc).timestamp())}",
                        action="reconciliation.drift",
                        title=title,
                        body=body,
                        level=level,
                        parsed_details={"before": before},
                        broker_details={"after": after},
                    )
                )
        db.commit()
    return updated


def publish_unresolved_intents(client) -> None:
    with SessionLocal() as db:
        intents = db.execute(
            select(TradeIntent).where(
                TradeIntent.state.in_([TradeIntentState.uncertain, TradeIntentState.reconciling])
            )
        ).scalars()
        sweep_bucket = _reconcile_sweep_bucket(datetime.now(timezone.utc))
        for intent in intents:
            event = CopyEvent.new(
                stream=StreamName.execution_intents,
                event_type="intent.reconcile",
                correlation_id=str(uuid.uuid4()),
                payload={"intent_id": str(intent.id), "connection_id": str(intent.connection_id)},
                idempotency_key=f"reconcile-sweep:{intent.id}:{sweep_bucket}",
            )
            RedisStreamBus(client).publish(event)

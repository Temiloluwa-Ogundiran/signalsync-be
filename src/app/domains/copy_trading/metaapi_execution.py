import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.delivery import DeliveryResult
from app.domains.copy_trading.engine import SignalAction
from app.domains.copy_trading.metaapi_broker import MetaApiBroker
from app.domains.copy_trading.metaapi_connections import get_metaapi_runtime
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
    if payload.get("symbol"):
        target = normalize_symbol(str(payload["symbol"]))
        return next(
            (trade for trade in trades if normalize_symbol(trade.signal_symbol).startswith(target)),
            None,
        )
    return trades[0] if len(trades) == 1 else None


def _record_activity(db, route, event: CopyEvent, action: str, title: str, level) -> None:
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
            parsed_details={},
            broker_details={},
        )
    )


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
        route = db.get(CopyRoute, intent.route_id)
        connection = db.get(CopyTradingConnection, intent.connection_id)
        parsed_action = db.get(ParsedAction, intent.parsed_action_id)
        lock = client.lock(
            f"copy:connection-lock:{intent.connection_id}", timeout=30, blocking_timeout=10
        )
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
            broker = MetaApiBroker(runtime.acquire(connection.metaapi_account_id))
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
            intent.state = TradeIntentState.submitted
            intent.submitted_at = datetime.now(timezone.utc)
            intent.attempt_count += 1
            db.commit()
            try:
                result, submitted_volume = _run_action(
                    runtime, broker, route, intent, payload, copied, selected
                )
            except Exception as exc:
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
                )
                db.commit()
                if not permanent:
                    _publish_reconcile(client, event, intent)
                return DeliveryResult.success()

            intent.state = TradeIntentState.confirmed
            intent.broker_result = result
            intent.resolved_at = datetime.now(timezone.utc)
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
            _record_activity(db, route, event, "broker.confirmed", "Copy trade confirmed", CopyActivityLevel.success)
            db.commit()
            return DeliveryResult.success()
        finally:
            lock.release()


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
            lock = client.lock(
                f"copy:connection-lock:{connection.id}", timeout=30, blocking_timeout=10
            )
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
                lock.release()
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
            select(CopiedTrade, CopyTradingConnection, TradeIntent)
            .join(CopyRoute, CopyRoute.id == CopiedTrade.route_id)
            .join(CopyTradingConnection, CopyTradingConnection.id == CopyRoute.target_connection_id)
            .join(TradeIntent, TradeIntent.id == CopiedTrade.intent_id)
            .where(CopiedTrade.lifecycle_state.in_(["open", "pending"]))
        ).all()
        brokers = {}
        runtime = get_metaapi_runtime()
        for trade, connection, source_intent in rows:
            if not connection.metaapi_account_id:
                continue
            broker = brokers.setdefault(
                connection.id,
                MetaApiBroker(runtime.acquire(connection.metaapi_account_id)),
            )
            positions = broker.positions()
            orders = broker.orders()
            if apply_broker_snapshot(
                trade,
                positions=positions,
                orders=orders,
                observed_at=datetime.now(timezone.utc),
                client_order_id=getattr(source_intent, "client_order_id", None),
            ):
                updated += 1
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

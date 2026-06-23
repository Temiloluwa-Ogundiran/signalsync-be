import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.accounts.models import TradingAccount
from app.domains.accounts.mt5_core_client import Mt5CoreClient, Mt5CoreClientHttpError, Mt5CoreClientJobFailed
from app.domains.copy_trading.engine import ParsedSignal, RouteExecutionPolicy, SignalAction, build_tp_legs, validate_signal
from app.domains.copy_trading.assembly import (
    ConversationCandidate,
    choose_conversation,
    merge_context,
    route_deadline,
)
from app.domains.copy_trading.delivery import DeliveryResult
from app.domains.copy_trading.execution import (
    calculate_signal_volume,
    catalog_fingerprint,
    client_order_id_for_key,
    ensure_exposure_within_limit,
)
from app.domains.copy_trading.generations import (
    OPEN_ACTIONS,
    corrective_action_for_submitted_edit,
    mark_generation_completed,
    mark_generation_expired,
    mark_generation_failed,
    mark_generation_submitted,
    merge_generation_context,
)
from app.domains.copy_trading.reconciliation import (
    apply_broker_snapshot,
    broker_result_matches_intent,
    should_retry_after_reconcile,
)
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyActivityLevel,
    CopyRoute,
    CopyRouteState,
    CopyTradingUserSettings,
    CopiedTrade,
    ParsedAction,
    RouteAssemblyState,
    RouteSignalAssembly,
    SignalConversation,
    SignalConversationState,
    SignalThread,
    SignalThreadState,
    SymbolMapping,
    TelegramSource,
    TelegramSourceType,
    TradeIntent,
    TradeIntentState,
)
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName
from app.domains.copy_trading.security import SessionCipher
from app.domains.copy_trading.symbols import BrokerSymbol, normalize_symbol, resolve_symbol
from app.shared.utils.encryption import decrypt_secret
from app.shared.utils.email import send_copy_trading_email
from app.domains.users.models import User


logger = logging.getLogger("copy-trading.signal")


def _is_permanent_broker_error(exc: Exception) -> bool:
    if bool(getattr(exc, "uncertain", False)):
        return False
    return isinstance(
        exc,
        (ValueError, Mt5CoreClientHttpError, Mt5CoreClientJobFailed),
    )


class AiAction(BaseModel):
    action: SignalAction
    symbol: str | None = None
    direction: str | None = None
    order_type: str | None = None
    entry: Decimal | None = None
    entry_high: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profits: list[Decimal] = Field(default_factory=list)
    close_fraction: Decimal | None = None
    explicit_reference: str | None = None
    confidence: float = Field(ge=0, le=1)


def _parse_message(text: str, context: dict) -> AiAction:
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings.COPY_TRADING_AI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.COPY_TRADING_AI_TIMEOUT_SECONDS,
        max_retries=settings.COPY_TRADING_AI_MAX_RETRIES,
    )
    parser = model.with_structured_output(AiAction, method="json_schema")
    return parser.invoke([
        ("system", "Parse one Telegram trading instruction. Return only the action described. Preserve exact prices. TP hit and SL hit are status_only. Never invent missing fields."),
        ("human", json.dumps({"known_signal_context": context, "message": text}, default=str)),
    ])


def _activity(
    db,
    *,
    route: CopyRoute,
    correlation_id: str,
    action: str,
    title: str,
    level: CopyActivityLevel,
    details: dict,
    raw_message: str | None = None,
) -> None:
    encrypted_raw_message = (
        SessionCipher(settings.ENCRYPTION_KEY).encrypt(raw_message)
        if raw_message
        else None
    )
    db.add(CopyActivityEvent(user_id=route.user_id, route_id=route.id, source_id=route.source_id, account_id=route.target_account_id, correlation_id=correlation_id, action=action, title=title, level=level, parsed_details=details, broker_details={}, encrypted_raw_message=encrypted_raw_message))


def _route_accepts_message(
    route: CopyRoute,
    source: TelegramSource,
    payload: dict,
) -> bool:
    if source.source_type != TelegramSourceType.group:
        return True
    return route.process_all_group_authors or bool(
        payload.get("sender_is_admin")
    )


def _select_copied_trade(
    trades: list[CopiedTrade],
    payload: dict,
) -> CopiedTrade | None:
    ordered = sorted(
        trades,
        key=lambda trade: trade.created_at,
        reverse=True,
    )
    signal_symbol = payload.get("symbol")
    if not signal_symbol:
        return ordered[0] if len(ordered) == 1 else None
    target = normalize_symbol(signal_symbol)
    for trade in ordered:
        candidate = normalize_symbol(trade.signal_symbol)
        if candidate.startswith(target) or target.startswith(candidate):
            return trade
    return None


def _assembly_for_action(db, parsed_action: ParsedAction | None):
    if parsed_action is None:
        return None
    assembly_id = (parsed_action.validation_result or {}).get("assembly_id")
    if not assembly_id:
        return None
    try:
        return db.get(RouteSignalAssembly, uuid.UUID(str(assembly_id)))
    except (TypeError, ValueError):
        return None


def _resolve_opening_generation(
    db,
    *,
    intent: TradeIntent,
    parsed_action: ParsedAction | None,
    failure_reason: str | None = None,
) -> None:
    if parsed_action is None or parsed_action.action_type not in OPEN_ACTIONS:
        return
    assembly = _assembly_for_action(db, parsed_action)
    if assembly is None:
        return
    now = datetime.now(timezone.utc)
    if failure_reason:
        mark_generation_failed(assembly, failure_reason, now)
        return
    sibling_intents = list(
        db.execute(
            select(TradeIntent).where(
                TradeIntent.parsed_action_id == intent.parsed_action_id
            )
        ).scalars()
    )
    if not sibling_intents:
        sibling_intents = [intent]
    if all(item.state == TradeIntentState.confirmed for item in sibling_intents):
        mark_generation_completed(assembly, now)


def expire_signal_threads(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    with SessionLocal() as db:
        expired_assemblies = list(
            db.execute(
                select(RouteSignalAssembly).where(
                    RouteSignalAssembly.state == RouteAssemblyState.assembling,
                    RouteSignalAssembly.assembly_deadline <= now,
                )
            )
            .scalars()
        )
        for assembly in expired_assemblies:
            route = db.get(CopyRoute, assembly.route_id)
            conversation = db.get(SignalConversation, assembly.conversation_id)
            if route and conversation:
                _activity(
                    db,
                    route=route,
                    correlation_id=conversation.correlation_id,
                    action="signal.expired",
                    title="Incomplete signal expired",
                    level=CopyActivityLevel.info,
                    details=assembly.context,
                )
            mark_generation_expired(
                assembly,
                "REQUIRED_DETAILS_TIMEOUT",
                now,
            )
        conversation_ids = {item.conversation_id for item in expired_assemblies}
        for conversation_id in conversation_ids:
            still_active = db.execute(
                select(RouteSignalAssembly.id).where(
                    RouteSignalAssembly.conversation_id == conversation_id,
                    RouteSignalAssembly.state.in_([
                        RouteAssemblyState.assembling,
                        RouteAssemblyState.ready,
                        RouteAssemblyState.executing,
                    ]),
                )
            ).first()
            if still_active is None:
                conversation = db.get(SignalConversation, conversation_id)
                if conversation:
                    conversation.state = SignalConversationState.expired
                    legacy = db.get(SignalThread, conversation.legacy_thread_id)
                    if legacy:
                        legacy.state = SignalThreadState.expired
        if expired_assemblies:
            db.commit()
        return len(expired_assemblies)


def _broker_timestamp(item: dict) -> float | None:
    for key in ("time_msc", "time_setup_msc", "time_done_msc"):
        value = item.get(key)
        if value:
            return float(value) / 1000
    for key in ("time", "time_setup", "time_done"):
        value = item.get(key)
        if value:
            return float(value)
    return None


def _reconciliation_accepts(
    intent: TradeIntent,
    data: dict,
    copied: CopiedTrade | None,
) -> bool:
    if getattr(intent, "client_order_id", None):
        return broker_result_matches_intent(intent, data, copied)
    action = intent.request_payload.get("action")
    positions = data.get("positions") or []
    orders = data.get("orders") or []
    history_orders = data.get("history_orders") or []
    deals = data.get("deals") or []
    submitted_at = getattr(intent, "submitted_at", None)
    cutoff = submitted_at.timestamp() - 5 if submitted_at else None

    def fresh(items: list[dict]) -> bool:
        if cutoff is None:
            return bool(items)
        return any(
            (timestamp := _broker_timestamp(item)) is not None
            and timestamp >= cutoff
            for item in items
        )

    if action in {
        SignalAction.open_market.value,
        SignalAction.place_pending.value,
        SignalAction.additional_tp.value,
    }:
        return fresh(positions + orders + history_orders + deals)
    if copied is None:
        return False
    position_id = str(copied.broker_position_id or "")
    order_id = str(copied.broker_order_id or "")
    if action in {SignalAction.modify_sl_tp.value, SignalAction.break_even.value}:
        expected_sl = intent.request_payload.get("stop_loss") or (
            intent.request_payload.get("entry")
            if action == SignalAction.break_even.value
            else None
        )
        expected_tp = intent.request_payload.get("take_profit")
        for position in positions:
            if str(position.get("ticket")) != position_id:
                continue
            sl_matches = expected_sl is None or Decimal(str(position.get("sl"))) == Decimal(str(expected_sl))
            tp_matches = expected_tp is None or Decimal(str(position.get("tp"))) == Decimal(str(expected_tp))
            return sl_matches and tp_matches
        return False
    if action == SignalAction.full_close.value:
        return not any(str(item.get("ticket")) == position_id for item in positions)
    if action == SignalAction.cancel_pending.value:
        return not any(str(item.get("ticket")) == order_id for item in orders)
    if action == SignalAction.partial_close.value:
        return fresh(deals)
    return False


def signal_handler(event: CopyEvent, client) -> DeliveryResult:
    if event.event_type == "message.deleted":
        _handle_deleted_message(event)
        return DeliveryResult.success()
    if event.event_type not in {"message.created", "message.edited"}:
        return DeliveryResult.success()
    source_id = uuid.UUID(event.payload["source_id"])
    lock = client.lock(
        f"copy:source-lock:{source_id}", timeout=30, blocking_timeout=5
    )
    if not lock.acquire(blocking=True):
        return DeliveryResult.retry("SOURCE_BUSY", "Signal source is busy.")
    try:
        with SessionLocal() as db:
            source = db.get(TelegramSource, source_id)
            if source is None or source.is_paused:
                return DeliveryResult.success()
            routes = list(db.execute(select(CopyRoute).where(CopyRoute.source_id == source_id, CopyRoute.state == CopyRouteState.active)).scalars())
            routes = [
                route
                for route in routes
                if _route_accepts_message(route, source, event.payload)
            ]
            if not routes:
                return DeliveryResult.success()
            now = datetime.now(timezone.utc)
            conversations = list(
                db.execute(
                    select(SignalConversation).where(
                        SignalConversation.source_id == source_id,
                        SignalConversation.state == SignalConversationState.active,
                    )
                ).scalars()
            )
            submitted_conversation_ids = {
                row[0]
                for row in db.execute(
                    select(RouteSignalAssembly.conversation_id).where(
                        RouteSignalAssembly.conversation_id.in_(
                            [item.id for item in conversations]
                        ),
                        RouteSignalAssembly.opening_intent_id.is_not(None),
                    )
                ).all()
            } if conversations else set()
            candidates = [
                ConversationCandidate(
                    id=item.id,
                    reply_root_message_id=item.reply_root_message_id,
                    last_message_id=item.last_message_id,
                    symbol=item.symbol,
                    direction=item.direction,
                    updated_at=item.updated_at,
                    opening_submitted=item.id in submitted_conversation_ids,
                )
                for item in conversations
            ]
            initial_context = (
                conversations[0].context
                if len(conversations) == 1
                and not event.payload.get("reply_to_message_id")
                else {}
            )
            try:
                parsed = _parse_message(event.payload["text"], initial_context)
            except Exception as exc:
                logger.warning(
                    "Signal parsing failed correlation_id=%s error_type=%s error=%s",
                    event.correlation_id,
                    type(exc).__name__,
                    exc,
                )
                return DeliveryResult.retry(type(exc).__name__.upper(), str(exc))

            choice = choose_conversation(
                reply_to_message_id=event.payload.get("reply_to_message_id"),
                message_id=event.payload.get("message_id"),
                is_edit=event.event_type == "message.edited",
                symbol=parsed.symbol,
                direction=parsed.direction,
                candidates=candidates,
                action=parsed.action.value,
            )
            if choice.ambiguous:
                for route in routes:
                    _activity(
                        db,
                        route=route,
                        correlation_id=event.correlation_id,
                        action="signal.ambiguous",
                        title="Signal update needs a clear reference",
                        level=CopyActivityLevel.warning,
                        details=parsed.model_dump(mode="json"),
                        raw_message=event.payload.get("text"),
                    )
                db.commit()
                return DeliveryResult.success()

            conversation = (
                db.get(SignalConversation, choice.selected.id)
                if choice.selected
                else None
            )
            if conversation is None:
                longest_window = max(route.assembly_window_seconds or 90 for route in routes)
                legacy = SignalThread(
                    source_id=source_id,
                    correlation_id=event.correlation_id,
                    state=SignalThreadState.assembling,
                    assembly_deadline=route_deadline(now, longest_window),
                    context={},
                    message_references=[],
                )
                db.add(legacy)
                db.flush()
                conversation = SignalConversation(
                    source_id=source_id,
                    legacy_thread_id=legacy.id,
                    correlation_id=event.correlation_id,
                    state=SignalConversationState.active,
                    reply_root_message_id=event.payload["message_id"],
                    explicit_reference=parsed.explicit_reference,
                    symbol=parsed.symbol,
                    direction=parsed.direction,
                    context={},
                    last_message_id=event.payload["message_id"],
                )
                db.add(conversation)
                db.flush()

            parsed_update = parsed.model_dump(mode="json")
            if (
                event.event_type == "message.edited"
                and choice.selected
                and choice.selected.opening_submitted
                and parsed.action.value in OPEN_ACTIONS
            ):
                parsed_update = corrective_action_for_submitted_edit(
                    conversation.context,
                    parsed_update,
                )
            conversation.context = merge_context(conversation.context, parsed_update)
            conversation.symbol = conversation.context.get("symbol")
            conversation.direction = conversation.context.get("direction")
            conversation.last_message_id = event.payload["message_id"]
            if event.event_type == "message.edited":
                conversation.last_message_revision += 1
            legacy = db.get(SignalThread, conversation.legacy_thread_id)
            if legacy:
                legacy.context = conversation.context
                legacy.symbol = conversation.symbol
                legacy.direction = conversation.direction
                legacy_refs = list(legacy.message_references)
                legacy_refs.append({
                    "connection_id": event.payload["connection_id"],
                    "chat_id": event.payload["chat_id"],
                    "message_id": event.payload["message_id"],
                    "revision": conversation.last_message_revision,
                })
                legacy.message_references = legacy_refs

            for route in routes:
                user_settings = db.get(CopyTradingUserSettings, route.user_id)
                policy_row = db.execute(select(CopyAccountPolicy).where(CopyAccountPolicy.account_id == route.target_account_id)).scalar_one_or_none()
                if (user_settings and user_settings.is_paused) or (policy_row and policy_row.is_paused):
                    continue
                assembly = db.execute(
                    select(RouteSignalAssembly).where(
                        RouteSignalAssembly.conversation_id == conversation.id,
                        RouteSignalAssembly.route_id == route.id,
                        RouteSignalAssembly.state.in_([
                            RouteAssemblyState.assembling,
                            RouteAssemblyState.ready,
                            RouteAssemblyState.executing,
                        ]),
                    )
                ).scalar_one_or_none()
                if assembly is None:
                    previous_generations = list(
                        db.execute(
                            select(RouteSignalAssembly.generation).where(
                                RouteSignalAssembly.conversation_id == conversation.id,
                                RouteSignalAssembly.route_id == route.id,
                            )
                        ).scalars()
                    )
                    assembly = RouteSignalAssembly(
                        conversation_id=conversation.id,
                        route_id=route.id,
                        state=RouteAssemblyState.assembling,
                        context=dict(conversation.context),
                        message_references=[],
                        generation=max(previous_generations or [0]) + 1,
                        assembly_deadline=route_deadline(now, route.assembly_window_seconds),
                    )
                    db.add(assembly)
                    db.flush()
                merged = merge_generation_context(
                    assembly.context,
                    parsed_update,
                    opening_submitted=bool(assembly.opening_intent_id),
                )
                assembly.context = merged
                if (
                    assembly.opening_action is None
                    and merged.get("action") in OPEN_ACTIONS
                ):
                    assembly.opening_action = merged["action"]
                refs = list(assembly.message_references)
                revision = 1 + max(
                    [
                        int(ref.get("revision", 0))
                        for ref in refs
                        if ref.get("message_id") == event.payload["message_id"]
                    ]
                    or [0]
                )
                refs.append({
                    "connection_id": event.payload["connection_id"],
                    "chat_id": event.payload["chat_id"],
                    "message_id": event.payload["message_id"],
                    "revision": revision,
                })
                assembly.message_references = refs
                signal = ParsedSignal(
                    action=SignalAction(merged["action"]),
                    symbol=merged.get("symbol"),
                    direction=merged.get("direction"),
                    entry=Decimal(str(merged["entry"])) if merged.get("entry") is not None else None,
                    entry_high=Decimal(str(merged["entry_high"])) if merged.get("entry_high") is not None else None,
                    stop_loss=Decimal(str(merged["stop_loss"])) if merged.get("stop_loss") is not None else None,
                    take_profits=[Decimal(str(value)) for value in merged.get("take_profits", [])],
                    close_fraction=Decimal(str(merged["close_fraction"])) if merged.get("close_fraction") is not None else None,
                    age_seconds=max(0, (now - datetime.fromisoformat(event.payload["occurred_at"])).total_seconds()),
                    confidence=float(parsed.confidence),
                )
                validation = validate_signal(signal, RouteExecutionPolicy(
                    minimum_fields=route.minimum_fields.value,
                    confidence_threshold=settings.COPY_TRADING_CONFIDENCE_THRESHOLD,
                    market_freshness_seconds=settings.COPY_TRADING_MARKET_FRESHNESS_SECONDS,
                    pending_orders_enabled=route.pending_orders_enabled,
                    allow_sl_tp_updates=route.allow_sl_tp_updates,
                    allow_break_even=route.allow_break_even,
                    allow_partial_close=route.allow_partial_close,
                    allow_full_close=route.allow_full_close,
                    allow_pending_cancel=route.allow_pending_cancel,
                    allow_additional_tp=route.allow_additional_tp,
                ))
                action = ParsedAction(thread_id=conversation.legacy_thread_id, route_id=route.id, telegram_message_id=event.payload["message_id"], action_type=signal.action.value, revision=revision, model_name=settings.COPY_TRADING_AI_MODEL, parser_version="v2", confidence=signal.confidence, payload=merged, validation_result={"accepted": validation.accepted, "reason": validation.reason, "advisory": validation.advisory, "assembly_id": str(assembly.id)})
                db.add(action)
                db.flush()
                if not validation.accepted:
                    title = validation.reason or "Signal skipped"
                    _activity(db, route=route, correlation_id=conversation.correlation_id, action="signal.waiting" if "waiting" in title.lower() else "signal.skipped", title=title, level=CopyActivityLevel.info, details=merged, raw_message=event.payload.get("text"))
                    continue
                active_copied_trades = list(
                    db.execute(
                        select(CopiedTrade)
                        .join(CopyRoute, CopyRoute.id == CopiedTrade.route_id)
                        .where(
                            CopyRoute.target_account_id == route.target_account_id,
                            CopiedTrade.lifecycle_state.in_(["open", "pending"]),
                        )
                    ).scalars()
                )
                current_exposure = sum(
                    (
                        Decimal(str(trade.current_volume or trade.original_volume or 0))
                        for trade in active_copied_trades
                    ),
                    Decimal("0"),
                )
                signal_volume = calculate_signal_volume(
                    fixed_lot=route.fixed_lot,
                    take_profit_count=len(signal.take_profits),
                    take_profit_mode=route.take_profit_mode.value,
                    distribution=route.lot_distribution.value,
                )
                try:
                    ensure_exposure_within_limit(
                        current_exposure=current_exposure,
                        signal_volume=signal_volume,
                        maximum=policy_row.max_lot,
                    )
                except ValueError as exc:
                    assembly.state = RouteAssemblyState.skipped
                    _activity(
                        db,
                        route=route,
                        correlation_id=conversation.correlation_id,
                        action="signal.skipped",
                        title=str(exc),
                        level=CopyActivityLevel.warning,
                        details=merged,
                        raw_message=event.payload.get("text"),
                    )
                    continue
                legs = build_tp_legs(fixed_lot=route.fixed_lot, take_profits=signal.take_profits, mode=route.take_profit_mode.value, distribution=route.lot_distribution.value) or [None]
                for index, leg in enumerate(legs):
                    intent_payload = dict(merged)
                    intent_payload["volume"] = str(leg.lot if leg else route.fixed_lot)
                    intent_payload["take_profit"] = str(leg.take_profit) if leg else None
                    intent_payload["conversation_id"] = str(conversation.id)
                    key = f"{event.payload['connection_id']}:{event.payload['chat_id']}:{event.payload['message_id']}:{revision}:{route.id}:{signal.action.value}:{index}"
                    intent = TradeIntent(user_id=route.user_id, route_id=route.id, account_id=route.target_account_id, parsed_action_id=action.id, idempotency_key=key, client_order_id=client_order_id_for_key(key), state=TradeIntentState.created, request_payload=intent_payload)
                    try:
                        with db.begin_nested():
                            db.add(intent)
                            db.flush()
                    except IntegrityError:
                        continue
                    if signal.action.value in OPEN_ACTIONS:
                        mark_generation_submitted(assembly, intent.id, now)
                    RedisStreamBus(client).publish(CopyEvent.new(stream=StreamName.execution_intents, event_type="intent.execute", correlation_id=conversation.correlation_id, payload={"intent_id": str(intent.id), "account_id": str(route.target_account_id)}, idempotency_key=key))
                _activity(db, route=route, correlation_id=conversation.correlation_id, action="signal.validated", title="Signal ready", level=CopyActivityLevel.info, details=merged, raw_message=event.payload.get("text"))
            db.commit()
            return DeliveryResult.success()
    finally:
        lock.release()


async def _submit_mt5(path: str, payload: dict) -> dict:
    client = Mt5CoreClient(poll_timeout=20, poll_interval=0.1)
    return await client.submit_action(path=path, payload=payload)


async def _broker_symbol(db, route: CopyRoute, account: TradingAccount, credentials: dict, signal_symbol: str, redis_client=None) -> str:
    normalized = normalize_symbol(signal_symbol)
    saved = db.execute(select(SymbolMapping).where(SymbolMapping.route_id == route.id, SymbolMapping.account_id == account.id, SymbolMapping.normalized_signal_symbol == normalized)).scalar_one_or_none()
    cache_key = f"copy:symbol-catalog:{account.id}"
    cached_catalog = redis_client.get(cache_key) if redis_client else None
    if cached_catalog:
        catalog = json.loads(cached_catalog)
    else:
        catalog_result = await _submit_mt5("/symbols", credentials)
        catalog = catalog_result.get("data", catalog_result).get("symbols", [])
        if redis_client:
            redis_client.setex(cache_key, 300, json.dumps(catalog, default=str))
    version = catalog_fingerprint(catalog)
    catalog_by_name = {str(item.get("name")): item for item in catalog}
    saved_item = catalog_by_name.get(saved.broker_symbol) if saved else None
    if saved and saved.catalog_version == version and saved_item and int(saved_item.get("trade_mode", 0) or 0) not in {0, 3}:
        return saved.broker_symbol
    candidates = [BrokerSymbol(name=item["name"], contract_size=Decimal(str(item.get("contract_size", 0))), spread=int(item.get("spread", 0)), trade_mode=int(item.get("trade_mode", 0)), visible=bool(item.get("visible", False))) for item in catalog]
    selected = resolve_symbol(signal_symbol, candidates)
    evidence = {"contract_size": str(selected.contract_size), "spread": selected.spread, "visible": selected.visible}
    if saved:
        saved.broker_symbol = selected.name
        saved.selection_evidence = evidence
        saved.catalog_version = version
    else:
        db.add(SymbolMapping(route_id=route.id, account_id=account.id, normalized_signal_symbol=normalized, broker_symbol=selected.name, selection_evidence=evidence, catalog_version=version))
    db.flush()
    return selected.name


def execution_handler(event: CopyEvent, client) -> None:
    if event.event_type == "emergency.execute":
        asyncio.run(_execute_emergency(event, client))
        return
    if event.event_type == "intent.reconcile":
        _reconcile_intent(event, client)
        return
    if event.event_type != "intent.execute":
        return
    intent_id = uuid.UUID(event.payload["intent_id"])
    with SessionLocal() as db:
        intent = db.get(TradeIntent, intent_id)
        if intent is None or intent.state not in {TradeIntentState.created, TradeIntentState.retryable}:
            return
        route = db.get(CopyRoute, intent.route_id)
        account = db.get(TradingAccount, intent.account_id)
        parsed_action = db.get(ParsedAction, intent.parsed_action_id)
        lock = client.lock(f"copy:account-lock:{intent.account_id}", timeout=30, blocking_timeout=10)
        if not lock.acquire(blocking=True):
            retry_count = int(intent.broker_result.get("lock_retries", 0)) + 1
            intent.broker_result = {
                **intent.broker_result,
                "lock_retries": retry_count,
            }
            if retry_count <= 3:
                intent.state = TradeIntentState.retryable
                RedisStreamBus(client).publish(
                    CopyEvent.new(
                        stream=StreamName.execution_intents,
                        event_type="intent.execute",
                        correlation_id=event.correlation_id,
                        payload={"intent_id": str(intent.id), "account_id": str(intent.account_id)},
                        idempotency_key=f"lock-retry:{intent.id}:{retry_count}",
                    )
                )
            else:
                intent.state = TradeIntentState.failed
                intent.last_error_code = "ACCOUNT_BUSY"
            db.commit()
            return
        try:
            user_settings = db.get(CopyTradingUserSettings, intent.user_id)
            account_policy = db.execute(
                select(CopyAccountPolicy).where(
                    CopyAccountPolicy.account_id == intent.account_id
                )
            ).scalar_one_or_none()
            if (
                route is None
                or route.state != CopyRouteState.active
                or (user_settings and user_settings.is_paused)
                or (account_policy and account_policy.is_paused)
            ):
                intent.state = TradeIntentState.failed
                intent.last_error_code = "AUTOMATION_PAUSED"
                db.commit()
                return
            if account is None or not account.encrypted_trader_password:
                intent.state = TradeIntentState.failed
                intent.last_error_code = "TRADER_CREDENTIALS_REQUIRED"
                db.commit()
                return
            password_value = account.encrypted_trader_password
            credentials = {"login": account.broker_login, "password": decrypt_secret(password_value), "server": account.broker_server}
            payload = intent.request_payload
            action = payload["action"]
            broker_symbol = asyncio.run(_broker_symbol(db, route, account, credentials, payload.get("symbol"), client)) if payload.get("symbol") else None
            order_payload = {**credentials, "symbol": broker_symbol, "volume": float(payload.get("volume", route.fixed_lot)), "sl": payload.get("stop_loss"), "tp": payload.get("take_profit"), "magic": route.magic_number, "comment": f"cp:{str(route.id)[:8]}", "client_order_id": intent.client_order_id}
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
            copied = _select_copied_trade(copied_trades, payload)
            if action in {SignalAction.open_market.value, SignalAction.additional_tp.value}:
                order_payload.update({"side": payload["direction"], "order_type": payload["direction"]})
                path = "/orders"
            elif action == SignalAction.place_pending.value:
                order_payload.update({"order_type": payload.get("order_type"), "price": payload.get("entry")})
                path = "/orders"
            elif action in {SignalAction.modify_sl_tp.value, SignalAction.break_even.value} and copied and copied.broker_position_id:
                order_payload = {**credentials, "sl": payload.get("stop_loss") or payload.get("entry"), "tp": payload.get("take_profit")}
                path = f"/positions/{copied.broker_position_id}/sl-tp"
            elif action in {SignalAction.partial_close.value, SignalAction.full_close.value} and copied and copied.broker_position_id:
                order_payload = {**credentials, "magic": route.magic_number, "comment": f"cp:{str(route.id)[:8]}"}
                if action == SignalAction.partial_close.value:
                    fraction = Decimal(str(payload.get("close_fraction") or "0.5"))
                    current_volume = Decimal(str(copied.current_volume or copied.original_volume or route.fixed_lot))
                    order_payload["volume"] = float(current_volume * fraction)
                path = f"/positions/{copied.broker_position_id}/close"
            elif action == SignalAction.cancel_pending.value and copied and copied.broker_order_id:
                order_payload = credentials
                path = f"/orders/{copied.broker_order_id}/cancel"
            else:
                raise ValueError(f"Management action {action} requires a matched copied trade.")
            intent.state = TradeIntentState.submitted
            intent.submitted_at = datetime.now(timezone.utc)
            intent.attempt_count += 1
            db.commit()
            try:
                result = asyncio.run(_submit_mt5(path, order_payload))
            except Exception as exc:
                intent = db.get(TradeIntent, intent_id)
                permanent = _is_permanent_broker_error(exc)
                intent.state = TradeIntentState.failed if permanent else TradeIntentState.uncertain
                intent.last_error_code = exc.__class__.__name__
                intent.broker_result = {"message": str(exc)}
                if permanent:
                    _resolve_opening_generation(
                        db,
                        intent=intent,
                        parsed_action=parsed_action,
                        failure_reason=intent.last_error_code,
                    )
                _activity(db, route=route, correlation_id=event.correlation_id, action="broker.failed" if permanent else "broker.uncertain", title=f"Failed: {exc}" if permanent else "Confirming broker status", level=CopyActivityLevel.error if permanent else CopyActivityLevel.warning, details=payload)
                db.commit()
                user = db.get(User, route.user_id)
                if user and route.notify_failure:
                    try:
                        send_copy_trading_email(user.email, subject="Copy trade failed", details={**payload, "reason": str(exc)})
                    except Exception:
                        logger.exception("Copy-trading failure email could not be sent user_id=%s intent_id=%s", user.id, intent.id)
                if not permanent:
                    RedisStreamBus(client).publish(
                        CopyEvent.new(
                            stream=StreamName.execution_intents,
                            event_type="intent.reconcile",
                            correlation_id=event.correlation_id,
                            payload={"intent_id": str(intent.id), "account_id": str(intent.account_id)},
                            idempotency_key=(
                                f"reconcile:{intent.id}:"
                                f"{intent.attempt_count}"
                            ),
                        )
                    )
                return
            intent = db.get(TradeIntent, intent_id)
            intent.state = TradeIntentState.confirmed
            intent.broker_result = result
            intent.resolved_at = datetime.now(timezone.utc)
            broker_order = result.get("order", result) if isinstance(result, dict) else {}
            if action in {SignalAction.open_market.value, SignalAction.place_pending.value, SignalAction.additional_tp.value}:
                submitted_volume = Decimal(str(payload.get("volume", route.fixed_lot)))
                db.add(CopiedTrade(route_id=route.id, thread_id=parsed_action.thread_id, intent_id=intent.id, magic_number=route.magic_number, route_comment=f"cp:{str(route.id)[:8]}", signal_symbol=str(payload.get("symbol")), broker_symbol=str(broker_symbol), broker_order_id=str(broker_order.get("order")) if broker_order.get("order") else None, broker_deal_id=str(broker_order.get("deal")) if broker_order.get("deal") else None, broker_position_id=str(broker_order.get("position") or broker_order.get("order")) if broker_order.get("position") or broker_order.get("order") else None, lifecycle_state="pending" if action == SignalAction.place_pending.value else "open", original_volume=submitted_volume, current_volume=submitted_volume, stop_loss=Decimal(str(payload["stop_loss"])) if payload.get("stop_loss") is not None else None, take_profit=Decimal(str(payload["take_profit"])) if payload.get("take_profit") is not None else None, broker_synced_at=datetime.now(timezone.utc)))
            elif copied and action == SignalAction.partial_close.value:
                copied.current_volume = max(Decimal("0"), Decimal(str(copied.current_volume or copied.original_volume or 0)) - Decimal(str(order_payload["volume"])))
                copied.broker_synced_at = datetime.now(timezone.utc)
            elif copied and action in {SignalAction.modify_sl_tp.value, SignalAction.break_even.value}:
                if order_payload.get("sl") is not None:
                    copied.stop_loss = Decimal(str(order_payload["sl"]))
                if order_payload.get("tp") is not None:
                    copied.take_profit = Decimal(str(order_payload["tp"]))
                copied.broker_synced_at = datetime.now(timezone.utc)
            elif copied and action in {SignalAction.full_close.value, SignalAction.cancel_pending.value}:
                copied.lifecycle_state = "closed" if action == SignalAction.full_close.value else "cancelled"
                copied.current_volume = Decimal("0")
                copied.broker_synced_at = datetime.now(timezone.utc)
            _resolve_opening_generation(
                db,
                intent=intent,
                parsed_action=parsed_action,
            )
            _activity(db, route=route, correlation_id=event.correlation_id, action="broker.succeeded", title="Trade opened" if action.startswith("open") else "Broker action completed", level=CopyActivityLevel.success, details=payload)
            db.commit()
            user = db.get(User, route.user_id)
            if user and route.notify_success:
                try:
                    send_copy_trading_email(user.email, subject="Copy trade completed", details={**payload, "broker_result": result})
                except Exception:
                    logger.exception("Copy-trading success email could not be sent user_id=%s intent_id=%s", user.id, intent.id)
        finally:
            lock.release()


def publish_unresolved_intents(client) -> None:
    with SessionLocal() as db:
        intents = list(db.execute(select(TradeIntent).where(TradeIntent.state.in_([TradeIntentState.uncertain, TradeIntentState.reconciling]))).scalars())
        for intent in intents:
            bucket = int(datetime.now(timezone.utc).timestamp() // 30)
            RedisStreamBus(client).publish(CopyEvent.new(stream=StreamName.execution_intents, event_type="intent.reconcile", correlation_id=str(uuid.uuid4()), payload={"intent_id": str(intent.id), "account_id": str(intent.account_id)}, idempotency_key=f"reconcile-sweep:{intent.id}:{intent.attempt_count}:{bucket}"))


def reconcile_copied_trades() -> int:
    observed_at = datetime.now(timezone.utc)
    updated = 0
    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(CopiedTrade, CopyRoute, TradingAccount)
                .join(CopyRoute, CopyRoute.id == CopiedTrade.route_id)
                .join(TradingAccount, TradingAccount.id == CopyRoute.target_account_id)
                .where(CopiedTrade.lifecycle_state.in_(["open", "pending"]))
            ).all()
        )
        grouped: dict[tuple[uuid.UUID, uuid.UUID], list[CopiedTrade]] = {}
        route_accounts: dict[tuple[uuid.UUID, uuid.UUID], tuple[CopyRoute, TradingAccount]] = {}
        for trade, route, account in rows:
            key = (route.id, account.id)
            grouped.setdefault(key, []).append(trade)
            route_accounts[key] = (route, account)
        for key, trades in grouped.items():
            route, account = route_accounts[key]
            if not account.encrypted_trader_password:
                continue
            credentials = {
                "login": account.broker_login,
                "password": decrypt_secret(account.encrypted_trader_password),
                "server": account.broker_server,
                "magic": route.magic_number,
                "comment": "",
            }
            try:
                result = asyncio.run(_submit_mt5("/account/reconcile", credentials))
            except Exception:
                logger.exception("Copied-trade reconciliation failed route_id=%s", route.id)
                continue
            data = result.get("data", result)
            for trade in trades:
                source_intent = db.get(TradeIntent, trade.intent_id)
                if apply_broker_snapshot(
                    trade,
                    positions=data.get("positions") or [],
                    orders=data.get("orders") or [],
                    observed_at=observed_at,
                    client_order_id=source_intent.client_order_id if source_intent else None,
                ):
                    updated += 1
        if rows:
            db.commit()
    return updated


def _reconcile_intent(event: CopyEvent, client) -> None:
    intent_id = uuid.UUID(event.payload["intent_id"])
    with SessionLocal() as db:
        intent = db.get(TradeIntent, intent_id)
        if intent is None or intent.state not in {TradeIntentState.uncertain, TradeIntentState.reconciling}:
            return
        route = db.get(CopyRoute, intent.route_id)
        account = db.get(TradingAccount, intent.account_id)
        if route is None or account is None:
            intent.state = TradeIntentState.failed
            intent.last_error_code = "ROUTE_OR_ACCOUNT_MISSING"
            db.commit()
            return
        intent.state = TradeIntentState.reconciling
        db.commit()
        credentials = {
            "login": account.broker_login,
            "password": decrypt_secret(account.encrypted_trader_password),
            "server": account.broker_server,
            "magic": route.magic_number,
            "comment": f"cp:{str(route.id)[:8]}",
            "client_order_id": getattr(intent, "client_order_id", None),
        }
        try:
            result = asyncio.run(_submit_mt5("/account/reconcile", credentials))
        except Exception as exc:
            intent = db.get(TradeIntent, intent_id)
            intent.state = TradeIntentState.uncertain
            intent.broker_result = {"message": str(exc)}
            db.commit()
            return
        data = result.get("data", result)
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
        copied = _select_copied_trade(copied_trades, intent.request_payload)
        accepted = _reconciliation_accepts(intent, data, copied)
        intent = db.get(TradeIntent, intent_id)
        if accepted:
            intent.state = TradeIntentState.confirmed
            intent.broker_result = data
            intent.resolved_at = datetime.now(timezone.utc)
            parsed_action_id = getattr(intent, "parsed_action_id", None)
            parsed_action = (
                db.get(ParsedAction, parsed_action_id)
                if parsed_action_id
                else None
            )
            _resolve_opening_generation(
                db,
                intent=intent,
                parsed_action=parsed_action,
            )
            _activity(
                db,
                route=route,
                correlation_id=event.correlation_id,
                action="broker.reconciled",
                title="Broker action confirmed",
                level=CopyActivityLevel.success,
                details=intent.request_payload,
            )
        elif should_retry_after_reconcile(intent, submission_started=bool(intent.submitted_at)):
            intent.state = TradeIntentState.retryable
            RedisStreamBus(client).publish(CopyEvent.new(stream=StreamName.execution_intents, event_type="intent.execute", correlation_id=event.correlation_id, payload={"intent_id": str(intent.id), "account_id": str(intent.account_id)}, idempotency_key=f"retry:{intent.id}:{intent.attempt_count}"))
        else:
            intent.state = TradeIntentState.uncertain
            _activity(db, route=route, correlation_id=event.correlation_id, action="broker.uncertain", title="Broker confirmation still pending", level=CopyActivityLevel.warning, details=intent.request_payload)
        db.commit()


def _handle_deleted_message(event: CopyEvent) -> None:
    with SessionLocal() as db:
        conversations = list(db.execute(select(SignalConversation).where(SignalConversation.source_id == uuid.UUID(event.payload["source_id"]))).scalars())
        for conversation in conversations:
            assemblies = list(db.execute(select(RouteSignalAssembly).where(RouteSignalAssembly.conversation_id == conversation.id)).scalars())
            for assembly in assemblies:
                if not any(ref.get("message_id") == event.payload["message_id"] for ref in assembly.message_references):
                    continue
                route = db.get(CopyRoute, assembly.route_id)
                if route is None:
                    continue
                if assembly.state in {RouteAssemblyState.assembling, RouteAssemblyState.ready}:
                    assembly.state = RouteAssemblyState.expired
                    _activity(db, route=route, correlation_id=conversation.correlation_id, action="signal.deleted", title="Signal removed before execution", level=CopyActivityLevel.warning, details={})
                else:
                    _activity(db, route=route, correlation_id=conversation.correlation_id, action="signal.deleted_after_execution", title="Source deleted an executed signal", level=CopyActivityLevel.warning, details={})
        db.commit()


async def _execute_emergency(event: CopyEvent, client) -> None:
    payload = event.payload
    with SessionLocal() as db:
        query = select(CopiedTrade, CopyRoute, TradingAccount).join(CopyRoute, CopyRoute.id == CopiedTrade.route_id).join(TradingAccount, TradingAccount.id == CopyRoute.target_account_id).where(CopyRoute.user_id == uuid.UUID(payload["user_id"]), CopiedTrade.lifecycle_state.in_(["open", "pending"]))
        scope = payload["scope"]
        scope_id = payload.get("scope_id")
        if scope == "account": query = query.where(CopyRoute.target_account_id == uuid.UUID(scope_id))
        elif scope == "source": query = query.where(CopyRoute.source_id == uuid.UUID(scope_id))
        elif scope == "route": query = query.where(CopyRoute.id == uuid.UUID(scope_id))
        rows = list(db.execute(query).all())
        for copied, route, account in rows:
            lock = client.lock(f"copy:account-lock:{account.id}", timeout=30, blocking_timeout=10)
            if not lock.acquire(blocking=True):
                _activity(db, route=route, correlation_id=event.correlation_id, action="emergency.failed", title="Emergency action delayed because the account is busy", level=CopyActivityLevel.error, details=payload)
                continue
            credentials = {"login": account.broker_login, "password": decrypt_secret(account.encrypted_trader_password), "server": account.broker_server}
            try:
                if payload["action"] in {"close_positions", "both"} and copied.lifecycle_state == "open" and copied.broker_position_id:
                    await _submit_mt5(f"/positions/{copied.broker_position_id}/close", credentials)
                    copied.lifecycle_state = "closed"
                if payload["action"] in {"cancel_pending", "both"} and copied.lifecycle_state == "pending" and copied.broker_order_id:
                    await _submit_mt5(f"/orders/{copied.broker_order_id}/cancel", credentials)
                    copied.lifecycle_state = "cancelled"
                _activity(db, route=route, correlation_id=event.correlation_id, action="emergency.succeeded", title="Emergency action completed", level=CopyActivityLevel.success, details=payload)
            except Exception as exc:
                _activity(db, route=route, correlation_id=event.correlation_id, action="emergency.failed", title="Emergency action failed", level=CopyActivityLevel.error, details={**payload, "reason": str(exc)})
            finally:
                lock.release()
        db.commit()

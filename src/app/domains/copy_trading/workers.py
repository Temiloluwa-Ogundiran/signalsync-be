import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from redis.exceptions import LockNotOwnedError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.client_ids import metaapi_client_id
from app.domains.copy_trading.engine import ParsedSignal, RouteExecutionPolicy, SignalAction, validate_signal
from app.domains.copy_trading.assembly import (
    ConversationCandidate,
    choose_conversation,
    merge_context,
    route_deadline,
)
from app.domains.copy_trading.delivery import DeliveryResult
from app.domains.copy_trading.execution import (
    ensure_account_risk_within_limits,
    intent_legs_for_action,
    signal_volume_for_action,
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
    _decimal_matches,
    broker_result_matches_intent,
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
    TelegramSource,
    TelegramSourceType,
    TradeIntent,
    TradeIntentState,
)
from app.domains.copy_trading.parser import AiAction, deterministic_parse
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName
from app.domains.copy_trading.security import SessionCipher
from app.domains.copy_trading.symbols import normalize_symbol


logger = logging.getLogger("copy-trading.signal")

_AI_SIGNAL_CUE = re.compile(
    r"\b(?:BUY|SELL|LONG|SHORT|SL|TP\d*|ENTRY|LIMIT|STOP|CLOSE|EXIT|"
    r"CANCEL|DELETE|BE|BREAK[ -]?EVEN|SECURE|RISK[ -]?FREE)\b",
    re.IGNORECASE,
)


def _source_lock(client, source_id):
    lease_seconds = max(
        120,
        int(
            settings.COPY_TRADING_AI_TIMEOUT_SECONDS
            * (settings.COPY_TRADING_AI_MAX_RETRIES + 1)
            + 30
        ),
    )
    return client.lock(
        f"copy:source-lock:{source_id}",
        timeout=lease_seconds,
        blocking_timeout=5,
    )


def _release_source_lock(lock, *, source_id) -> None:
    try:
        lock.release()
    except LockNotOwnedError:
        logger.warning(
            "Source lock expired before release source_id=%s",
            source_id,
        )


def _semantic_fingerprint(route: CopyRoute, payload: dict) -> str:
    meaningful = {
        key: payload.get(key)
        for key in (
            "action", "symbol", "direction", "entry", "entry_high",
            "stop_loss", "take_profits", "close_fraction",
        )
    }
    encoded = json.dumps(meaningful, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{route.id}:{encoded}".encode("utf-8")).hexdigest()


def _load_existing_conversation_for_correlation(
    db,
    *,
    source_id: uuid.UUID,
    correlation_id: str,
) -> SignalConversation | None:
    return db.execute(
        select(SignalConversation).where(
            SignalConversation.source_id == source_id,
            SignalConversation.correlation_id == correlation_id,
        )
    ).scalar_one_or_none()


def _parse_message(text: str, context: dict) -> AiAction:
    deterministic = deterministic_parse(text)
    if deterministic is not None:
        return deterministic
    if not _AI_SIGNAL_CUE.search(text or "") and not (
        context and re.search(r"\d", text or "")
    ):
        return AiAction(action=SignalAction.status_only, confidence=0)
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


def _is_user_visible_status(parsed: AiAction) -> bool:
    return (
        parsed.action == SignalAction.status_only
        and parsed.confidence >= 1
        and parsed.symbol is not None
    )


def _safe_activity_title(title: str) -> str:
    if len(title) <= 200:
        return title
    return f"{title[:197]}..."


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
    db.add(CopyActivityEvent(user_id=route.user_id, route_id=route.id, source_id=route.source_id, connection_id=route.target_connection_id, correlation_id=correlation_id, action=action, title=_safe_activity_title(title), level=level, parsed_details=details, broker_details={}, encrypted_raw_message=encrypted_raw_message))


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
        if conversation_ids:
            db.flush()
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
            sl_matches = expected_sl is None or _decimal_matches(
                position.get("sl"), expected_sl
            )
            tp_matches = expected_tp is None or _decimal_matches(
                position.get("tp"), expected_tp
            )
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
    if settings.COPY_TRADING_GLOBAL_PAUSED:
        return DeliveryResult.success()
    source_id = uuid.UUID(event.payload["source_id"])
    lock = _source_lock(client, source_id)
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
            active_thread_ids = {
                row[0]
                for row in db.execute(
                    select(CopiedTrade.thread_id).where(
                        CopiedTrade.route_id.in_([route.id for route in routes]),
                        CopiedTrade.lifecycle_state.in_(["open", "pending"]),
                    )
                ).all()
            }
            candidates = [
                ConversationCandidate(
                    id=item.id,
                    reply_root_message_id=item.reply_root_message_id,
                    last_message_id=item.last_message_id,
                    symbol=item.symbol,
                    direction=item.direction,
                    updated_at=item.updated_at,
                    opening_submitted=item.id in submitted_conversation_ids,
                    has_active_trade=item.legacy_thread_id in active_thread_ids,
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

            if parsed.action == SignalAction.status_only:
                if _is_user_visible_status(parsed):
                    for route in routes:
                        _activity(
                            db,
                            route=route,
                            correlation_id=event.correlation_id,
                            action="signal.status",
                            title="Channel status update",
                            level=CopyActivityLevel.info,
                            details=parsed.model_dump(mode="json"),
                            raw_message=event.payload.get("text"),
                        )
                    db.commit()
                return DeliveryResult.success()

            existing_conversation = _load_existing_conversation_for_correlation(
                db,
                source_id=source_id,
                correlation_id=event.correlation_id,
            )
            if existing_conversation is not None:
                choice = ConversationCandidate(
                    id=existing_conversation.id,
                    reply_root_message_id=existing_conversation.reply_root_message_id,
                    last_message_id=existing_conversation.last_message_id,
                    symbol=existing_conversation.symbol,
                    direction=existing_conversation.direction,
                    updated_at=existing_conversation.updated_at,
                    opening_submitted=existing_conversation.id in submitted_conversation_ids,
                    has_active_trade=(
                        existing_conversation.legacy_thread_id in active_thread_ids
                    ),
                )
                ambiguous = False
            else:
                selected_choice = choose_conversation(
                    reply_to_message_id=event.payload.get("reply_to_message_id"),
                    message_id=event.payload.get("message_id"),
                    is_edit=event.event_type == "message.edited",
                    symbol=parsed.symbol,
                    direction=parsed.direction,
                    candidates=candidates,
                    action=parsed.action.value,
                )
                choice = selected_choice.selected
                ambiguous = selected_choice.ambiguous
            if ambiguous:
                logger.info(
                    "Skipped ambiguous signal update correlation_id=%s source_id=%s candidates=%s",
                    event.correlation_id,
                    source_id,
                    len(candidates),
                )
                for route in routes:
                    _activity(
                        db,
                        route=route,
                        correlation_id=event.correlation_id,
                        action="signal.skipped",
                        title="Unclear channel update skipped",
                        level=CopyActivityLevel.info,
                        details=parsed.model_dump(mode="json"),
                        raw_message=event.payload.get("text"),
                    )
                db.commit()
                return DeliveryResult.success()

            conversation = (
                existing_conversation
                if existing_conversation is not None
                else db.get(SignalConversation, choice.id)
                if choice
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
                and choice
                and choice.opening_submitted
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
                policy_row = db.execute(select(CopyAccountPolicy).where(CopyAccountPolicy.connection_id == route.target_connection_id)).scalar_one_or_none()
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
                    market_freshness_seconds=policy_row.market_signal_max_age_seconds,
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
                            CopyRoute.target_connection_id == route.target_connection_id,
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
                signal_volume = signal_volume_for_action(
                    action=signal.action,
                    fixed_lot=route.fixed_lot,
                    take_profit_count=len(signal.take_profits),
                    take_profit_mode=route.take_profit_mode.value,
                    distribution=route.lot_distribution.value,
                )
                if signal_volume > 0:
                    try:
                        ensure_account_risk_within_limits(
                            symbol=signal.symbol or "",
                            signal_volume=signal_volume,
                            current_exposure=current_exposure,
                            current_positions=len(active_copied_trades),
                            equity=None,
                            daily_equity_anchor=None,
                            peak_equity=None,
                            max_lot_per_trade=policy_row.max_lot_per_trade,
                            max_total_lot=policy_row.max_lot,
                            max_open_positions=policy_row.max_open_positions,
                            daily_loss_limit=None,
                            max_drawdown_percent=None,
                            allowed_symbols=policy_row.allowed_symbols,
                            blocked_symbols=policy_row.blocked_symbols,
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
                duplicate_window = route.semantic_duplicate_window_seconds
                if duplicate_window > 0:
                    fingerprint = _semantic_fingerprint(route, merged)
                    accepted = client.set(
                        f"copy:semantic:{fingerprint}",
                        event.correlation_id,
                        nx=True,
                        ex=duplicate_window,
                    )
                    if not accepted:
                        assembly.state = RouteAssemblyState.skipped
                        _activity(
                            db,
                            route=route,
                            correlation_id=conversation.correlation_id,
                            action="signal.duplicate",
                            title="Duplicate signal ignored",
                            level=CopyActivityLevel.info,
                            details=merged,
                            raw_message=event.payload.get("text"),
                        )
                        continue
                legs = intent_legs_for_action(
                    action=signal.action,
                    fixed_lot=route.fixed_lot,
                    take_profits=signal.take_profits,
                    mode=route.take_profit_mode.value,
                    distribution=route.lot_distribution.value,
                )
                for index, leg in enumerate(legs):
                    intent_payload = dict(merged)
                    intent_payload["volume"] = str(leg.lot if leg else route.fixed_lot)
                    intent_payload["take_profit"] = (
                        str(leg.take_profit)
                        if leg
                        else str(signal.take_profits[-1])
                        if signal.take_profits
                        else None
                    )
                    intent_payload["conversation_id"] = str(conversation.id)
                    intent_payload["_telemetry"] = {
                        "correlation_id": conversation.correlation_id,
                        "telegram_at": event.payload.get("occurred_at"),
                        "ingested_at": event.occurred_at,
                        "validated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    key = f"{event.payload['connection_id']}:{event.payload['chat_id']}:{event.payload['message_id']}:{revision}:{route.id}:{signal.action.value}:{index}"
                    intent_id = uuid.uuid4()
                    intent = TradeIntent(
                        id=intent_id,
                        user_id=route.user_id,
                        route_id=route.id,
                        connection_id=route.target_connection_id,
                        legacy_account_id=route.legacy_target_account_id,
                        parsed_action_id=action.id,
                        idempotency_key=key,
                        client_order_id=metaapi_client_id(route_id=route.id, intent_id=intent_id),
                        state=TradeIntentState.created,
                        request_payload=intent_payload,
                    )
                    try:
                        with db.begin_nested():
                            db.add(intent)
                            db.flush()
                    except IntegrityError:
                        continue
                    if signal.action.value in OPEN_ACTIONS:
                        mark_generation_submitted(assembly, intent.id, now)
                    RedisStreamBus(client).publish(CopyEvent.new(stream=StreamName.execution_intents, event_type="intent.execute", correlation_id=conversation.correlation_id, payload={"intent_id": str(intent.id), "connection_id": str(route.target_connection_id)}, idempotency_key=key))
                _activity(db, route=route, correlation_id=conversation.correlation_id, action="signal.validated", title="Signal ready", level=CopyActivityLevel.info, details=merged, raw_message=event.payload.get("text"))
            db.commit()
            return DeliveryResult.success()
    finally:
        _release_source_lock(lock, source_id=source_id)


def _handle_deleted_message(event: CopyEvent) -> None:
    with SessionLocal() as db:
        conversations = list(
            db.execute(
                select(SignalConversation).where(
                    SignalConversation.source_id == uuid.UUID(event.payload["source_id"])
                )
            ).scalars()
        )
        for conversation in conversations:
            assemblies = list(
                db.execute(
                    select(RouteSignalAssembly).where(
                        RouteSignalAssembly.conversation_id == conversation.id
                    )
                ).scalars()
            )
            for assembly in assemblies:
                if not any(
                    ref.get("message_id") == event.payload["message_id"]
                    for ref in assembly.message_references
                ):
                    continue
                route = db.get(CopyRoute, assembly.route_id)
                if route is None:
                    continue
                if assembly.state in {
                    RouteAssemblyState.assembling,
                    RouteAssemblyState.ready,
                }:
                    assembly.state = RouteAssemblyState.expired
                    action = "signal.deleted"
                    title = "Signal removed before execution"
                else:
                    action = "signal.deleted_after_execution"
                    title = "Source deleted an executed signal"
                _activity(
                    db,
                    route=route,
                    correlation_id=conversation.correlation_id,
                    action=action,
                    title=title,
                    level=CopyActivityLevel.warning,
                    details={},
                )
        db.commit()

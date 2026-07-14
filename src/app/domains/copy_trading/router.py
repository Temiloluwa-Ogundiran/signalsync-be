import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
import json
import redis
import redis.asyncio as async_redis
import time
import uuid as uuid_module
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.copy_trading import live_updates as _live_updates  # noqa: F401
from app.domains.copy_trading import service
from app.domains.copy_trading.schemas import (
    CopyAccountPolicyResponse,
    CopyAccountPolicyUpdate,
    CopyActivityResponse,
    CopyActivityPageResponse,
    CopyDeadLetterResponse,
    CopyLaunchReadinessResponse,
    CopySystemHealthResponse,
    CopyRouteCreate,
    CopyRouteResponse,
    CopyRouteUpdate,
    CopyTradingSettingsResponse,
    CopyTradingSettingsUpdate,
    CopyTradingConnectionCreate,
    CopyTradingConnectionResponse,
    EmergencyActionRequest,
    TelegramAuthResponse,
    TelegramCodeSubmit,
    TelegramConnectionResponse,
    TelegramDialogResponse,
    TelegramPasswordSubmit,
    TelegramPhoneAuthStart,
    TelegramSourceCreate,
    TelegramSourceResponse,
    CopyRoutePreviewRequest,
    CopyRoutePreviewResponse,
    CopyExecutionLatencyResponse,
    CopySignalReviewResponse,
    CopySignalReviewApprove,
)
from app.core.config import settings
from app.domains.copy_trading import repository as repo
from app.domains.copy_trading.models import CopyActivityLevel, CopyDeadLetter, CopyWorkerHealth, DeadLetterState, TelegramAuthAttempt, TelegramAuthState, TelegramConnection, TelegramSource, TelegramSourceState, TradeIntent, TradeIntentState
from app.domains.copy_trading.health import add_metaapi_health, aggregate_health, build_launch_readiness
from app.domains.copy_trading.telegram_auth import decode_auth_state, encode_auth_state
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName
from app.domains.copy_trading.security import SessionCipher
from app.domains.copy_trading import operator_tools
from app.domains.users.models import User
from app.shared.deps import get_current_user


router = APIRouter(prefix="/copy-trading", tags=["copy-trading"])


def _redis_client():
    return redis.Redis.from_url(settings.COPY_TRADING_REDIS_URL, decode_responses=True)


@router.get("/live")
async def copy_trading_live_updates(
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    async def events():
        client = async_redis.Redis.from_url(
            settings.COPY_TRADING_REDIS_URL,
            decode_responses=True,
        )
        pubsub = client.pubsub()
        channel = f"copy:live:{current_user.id}"
        await pubsub.subscribe(channel)
        try:
            yield 'data: {"type":"connected"}\n\n'
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=15,
                )
                if message:
                    yield f"data: {message['data']}\n\n"
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            await client.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _publish_command(event_type: str, correlation_id: str, payload: dict, key: str) -> None:
    RedisStreamBus(_redis_client()).publish(CopyEvent.new(stream=StreamName.telegram_commands, event_type=event_type, correlation_id=correlation_id, payload=payload, idempotency_key=key))


def _publish_metaapi_command(connection, *, event_type: str) -> None:
    RedisStreamBus(_redis_client()).publish(
        CopyEvent.new(
            stream=StreamName.metaapi_provisioning,
            event_type=event_type,
            correlation_id=str(connection.id),
            payload={"connection_id": str(connection.id)},
            idempotency_key=(
                f"{event_type}:{connection.id}:{connection.provisioning_transaction_id}"
            ),
        )
    )


@router.get(
    "/connections", response_model=list[CopyTradingConnectionResponse]
)
def list_copy_connections(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopyTradingConnectionResponse]:
    return [
        CopyTradingConnectionResponse.model_validate(item)
        for item in service.list_copy_connections(db, current_user=current_user)
    ]


@router.post(
    "/connections",
    response_model=CopyTradingConnectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_copy_connection(
    payload: CopyTradingConnectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingConnectionResponse:
    connection = service.create_copy_connection(
        db, current_user=current_user, payload=payload
    )
    _publish_metaapi_command(connection, event_type="connection.provision")
    return CopyTradingConnectionResponse.model_validate(connection)


@router.get(
    "/connections/{connection_id}", response_model=CopyTradingConnectionResponse
)
def get_copy_connection(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingConnectionResponse:
    return CopyTradingConnectionResponse.model_validate(
        service.get_copy_connection(
            db, current_user=current_user, connection_id=connection_id
        )
    )


@router.post(
    "/connections/{connection_id}/retry",
    response_model=CopyTradingConnectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_copy_connection(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingConnectionResponse:
    connection = service.retry_copy_connection(
        db, current_user=current_user, connection_id=connection_id
    )
    _publish_metaapi_command(connection, event_type="connection.provision")
    return CopyTradingConnectionResponse.model_validate(connection)


@router.delete(
    "/connections/{connection_id}",
    response_model=CopyTradingConnectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def delete_copy_connection(
    connection_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingConnectionResponse:
    connection = service.delete_copy_connection(
        db, current_user=current_user, connection_id=connection_id
    )
    if connection.state.value != "deleted":
        _publish_metaapi_command(connection, event_type="connection.delete")
    return CopyTradingConnectionResponse.model_validate(connection)


def _require_telegram_configuration() -> None:
    if not settings.COPY_TRADING_ENABLED or not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        raise HTTPException(status_code=503, detail="Telegram connection is temporarily unavailable while service credentials are being configured.")


def _request_live_dialogs(
    client,
    connection_id: uuid.UUID,
    *,
    timeout_seconds: float = 1.5,
) -> list[dict]:
    request_id = str(uuid_module.uuid4())
    response_key = f"copy:telegram:dialogs-response:{request_id}"
    _publish_command(
        "dialogs.refresh",
        request_id,
        {
            "connection_id": str(connection_id),
            "request_id": request_id,
        },
        f"dialogs-refresh:{request_id}",
    )
    cached = client.get(f"copy:telegram:dialogs:{connection_id}")
    if cached:
        return json.loads(cached)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        raw = client.get(response_key)
        if raw:
            client.delete(response_key)
            result = json.loads(raw)
            if isinstance(result, dict) and result.get("error"):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=result["error"],
                )
            return result
        time.sleep(0.05)
    return []


def _owned_auth(auth_id: uuid.UUID, current_user: User, db: Session) -> dict:
    attempt = db.query(TelegramAuthAttempt).filter(
        TelegramAuthAttempt.auth_id == str(auth_id),
        TelegramAuthAttempt.user_id == current_user.id,
    ).one_or_none()
    if attempt is None or attempt.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="Telegram sign-in was not found or has expired.")
    data = decode_auth_state(attempt, SessionCipher(settings.ENCRYPTION_KEY))
    data.update({"method": attempt.method, "state": data.get("state", attempt.state.value), "message": attempt.message or data.get("message", "Processing")})
    return data


def _create_auth_attempt(db: Session, *, connection: TelegramConnection, user_id: uuid.UUID, method: str, initial: dict) -> TelegramAuthAttempt:
    cipher = SessionCipher(settings.ENCRYPTION_KEY)
    attempt = TelegramAuthAttempt(
        auth_id=str(connection.id),
        user_id=user_id,
        connection_id=connection.id,
        method=method,
        state=TelegramAuthState.pending,
        encrypted_state=encode_auth_state(initial, cipher),
        message=initial["message"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(attempt)
    return attempt


@router.get("/settings", response_model=CopyTradingSettingsResponse)
def get_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingSettingsResponse:
    settings = service.get_user_settings(db, current_user=current_user)
    return CopyTradingSettingsResponse.model_validate(settings)


@router.patch("/settings", response_model=CopyTradingSettingsResponse)
def update_settings(
    payload: CopyTradingSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyTradingSettingsResponse:
    settings = service.update_user_settings(db, current_user=current_user, payload=payload)
    return CopyTradingSettingsResponse.model_validate(settings)


@router.get("/account-policies", response_model=list[CopyAccountPolicyResponse])
def list_account_policies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopyAccountPolicyResponse]:
    policies = service.list_account_policies(db, current_user=current_user)
    return [CopyAccountPolicyResponse.model_validate(policy) for policy in policies]


@router.patch(
    "/account-policies/{connection_id}", response_model=CopyAccountPolicyResponse
)
def update_account_policy(
    connection_id: uuid.UUID,
    payload: CopyAccountPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyAccountPolicyResponse:
    policy = service.update_account_policy(
        db,
        current_user=current_user,
        connection_id=connection_id,
        payload=payload,
    )
    return CopyAccountPolicyResponse.model_validate(policy)


@router.get("/routes", response_model=list[CopyRouteResponse])
def list_routes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopyRouteResponse]:
    routes = service.list_routes(db, current_user=current_user)
    return [CopyRouteResponse.model_validate(route) for route in routes]


@router.post(
    "/routes", response_model=CopyRouteResponse, status_code=status.HTTP_201_CREATED
)
def create_route(
    payload: CopyRouteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.create_route(db, current_user=current_user, payload=payload)
    return CopyRouteResponse.model_validate(route)


@router.get("/routes/{route_id}", response_model=CopyRouteResponse)
def get_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.get_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)


@router.post("/routes/{route_id}/preview", response_model=CopyRoutePreviewResponse)
def preview_route(
    route_id: uuid.UUID,
    payload: CopyRoutePreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRoutePreviewResponse:
    route = service.get_route(db, current_user=current_user, route_id=route_id)
    return CopyRoutePreviewResponse.model_validate(
        operator_tools.preview_route(
            db,
            current_user=current_user,
            route=route,
            text=payload.text,
            occurred_at=payload.occurred_at,
        )
    )


@router.patch("/routes/{route_id}", response_model=CopyRouteResponse)
def update_route(
    route_id: uuid.UUID,
    payload: CopyRouteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.update_route(
        db, current_user=current_user, route_id=route_id, payload=payload
    )
    return CopyRouteResponse.model_validate(route)


@router.post("/routes/{route_id}/pause", response_model=CopyRouteResponse)
def pause_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.pause_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)


@router.post("/routes/{route_id}/resume", response_model=CopyRouteResponse)
def resume_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.resume_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)


@router.post("/routes/{route_id}/activate", response_model=CopyRouteResponse)
def activate_route(route_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return CopyRouteResponse.model_validate(service.activate_route(db, current_user=current_user, route_id=route_id))


@router.delete("/routes/{route_id}", status_code=204)
def delete_route(route_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    service.delete_route(db, current_user=current_user, route_id=route_id)


@router.get("/activity", response_model=CopyActivityPageResponse)
def list_activity(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = None,
    level: Optional[CopyActivityLevel] = None,
    source_id: Optional[uuid.UUID] = None,
    connection_id: Optional[uuid.UUID] = None,
    search: Optional[str] = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyActivityPageResponse:
    try:
        before = datetime.fromisoformat(cursor) if cursor else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Activity cursor is invalid.") from exc
    events = service.list_activity(
        db,
        current_user=current_user,
        limit=limit + 1,
        before=before,
        level=level,
        source_id=source_id,
        connection_id=connection_id,
        search=search,
    )
    has_more = len(events) > limit
    visible = events[:limit]
    next_cursor = visible[-1].created_at.isoformat() if has_more and visible else None
    return CopyActivityPageResponse(items=[CopyActivityResponse.model_validate(event) for event in visible], next_cursor=next_cursor)


@router.get("/latency", response_model=CopyExecutionLatencyResponse)
def copy_latency(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyExecutionLatencyResponse:
    return CopyExecutionLatencyResponse.model_validate(
        operator_tools.latency_summary(db, user_id=current_user.id)
    )


@router.get("/signal-reviews", response_model=list[CopySignalReviewResponse])
def signal_reviews(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopySignalReviewResponse]:
    return [
        CopySignalReviewResponse.model_validate(item)
        for item in operator_tools.list_reviews(db, user_id=current_user.id)
    ]


@router.post("/signal-reviews/{review_id}/approve", response_model=CopySignalReviewResponse)
def approve_signal_review(
    review_id: uuid.UUID,
    payload: CopySignalReviewApprove,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopySignalReviewResponse:
    item = operator_tools.resolve_review(
        db,
        user_id=current_user.id,
        review_id=review_id,
        conversation_id=payload.conversation_id,
        client=_redis_client(),
    )
    return CopySignalReviewResponse.model_validate(item)


@router.post("/signal-reviews/{review_id}/ignore", response_model=CopySignalReviewResponse)
def ignore_signal_review(
    review_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopySignalReviewResponse:
    item = operator_tools.resolve_review(
        db,
        user_id=current_user.id,
        review_id=review_id,
        conversation_id=None,
        client=_redis_client(),
    )
    return CopySignalReviewResponse.model_validate(item)


@router.get("/health", response_model=CopySystemHealthResponse)
def copy_system_health(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    heartbeats = list(db.execute(select(CopyWorkerHealth)).scalars())
    health = add_metaapi_health(
        aggregate_health(heartbeats),
        copy_trading_enabled=settings.COPY_TRADING_ENABLED,
        metaapi_enabled=settings.COPY_TRADING_METAAPI_ENABLED,
        token_configured=bool(settings.METAAPI_TOKEN),
    )
    return CopySystemHealthResponse.model_validate(health.__dict__)


@router.get("/launch-readiness", response_model=CopyLaunchReadinessResponse)
def copy_launch_readiness(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    health = add_metaapi_health(
        aggregate_health(list(db.execute(select(CopyWorkerHealth)).scalars())),
        copy_trading_enabled=settings.COPY_TRADING_ENABLED,
        metaapi_enabled=settings.COPY_TRADING_METAAPI_ENABLED,
        token_configured=bool(settings.METAAPI_TOKEN),
    )
    dead_letter_count = len(
        list(
            db.execute(
                select(CopyDeadLetter.id).where(
                    CopyDeadLetter.user_id == current_user.id,
                    CopyDeadLetter.state == DeadLetterState.pending,
                )
            ).all()
        )
    )
    uncertain_created_at = list(
        db.execute(
            select(TradeIntent.created_at).where(
                TradeIntent.user_id == current_user.id,
                TradeIntent.state.in_(
                    [
                        TradeIntentState.uncertain,
                        TradeIntentState.reconciling,
                    ]
                ),
            )
        ).scalars()
    )
    active_created_at = list(
        db.execute(
            select(TradeIntent.created_at).where(
                TradeIntent.user_id == current_user.id,
                TradeIntent.state.in_(
                    [
                        TradeIntentState.created,
                        TradeIntentState.retryable,
                        TradeIntentState.submitted,
                    ]
                ),
            )
        ).scalars()
    )
    readiness = build_launch_readiness(
        health,
        dead_letter_count=dead_letter_count,
        uncertain_intent_ages=[
            max(0, int((now - created_at).total_seconds()))
            for created_at in uncertain_created_at
        ],
        active_intent_ages=[
            max(0, int((now - created_at).total_seconds()))
            for created_at in active_created_at
        ],
        active_intent_max_age_seconds=settings.COPY_TRADING_UNCERTAIN_MAX_AGE_SECONDS,
        uncertain_max_age_seconds=settings.COPY_TRADING_UNCERTAIN_MAX_AGE_SECONDS,
        global_paused=settings.COPY_TRADING_GLOBAL_PAUSED,
    )
    return CopyLaunchReadinessResponse.model_validate(readiness.__dict__)


@router.get("/dead-letters", response_model=list[CopyDeadLetterResponse])
def list_dead_letters(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    items = list(db.execute(select(CopyDeadLetter).where(CopyDeadLetter.user_id == current_user.id).order_by(CopyDeadLetter.created_at.desc()).limit(100)).scalars())
    return [CopyDeadLetterResponse.model_validate(item) for item in items]


@router.post("/dead-letters/{dead_letter_id}/replay", response_model=CopyDeadLetterResponse)
def replay_dead_letter(dead_letter_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = db.execute(select(CopyDeadLetter).where(CopyDeadLetter.id == dead_letter_id, CopyDeadLetter.user_id == current_user.id)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Failed event not found.")
    if item.state != DeadLetterState.pending:
        raise HTTPException(
            status_code=409,
            detail="This failed event has already been retried.",
        )
    fields = dict(item.event_payload)
    event = CopyEvent.from_fields(fields)
    replay = CopyEvent.new(stream=event.stream, event_type=event.event_type, correlation_id=event.correlation_id, payload=event.payload, idempotency_key=f"replay:{item.id}:{uuid_module.uuid4()}")
    RedisStreamBus(_redis_client()).publish(replay)
    item.state = DeadLetterState.replayed
    item.replayed_at = datetime.now(timezone.utc)
    service.record_activity(
        db,
        user_id=current_user.id,
        correlation_id=item.correlation_id,
        action="dead_letter.replayed",
        title="Failed copy action retried",
        body=(
            "TradePartna sent the failed action through the pipeline one more time. "
            "Its result will appear in Activity."
        ),
        level=CopyActivityLevel.info,
        parsed_details={
            "error_code": item.error_code,
            "attempts": item.attempts,
        },
    )
    db.commit(); db.refresh(item)
    return CopyDeadLetterResponse.model_validate(item)


@router.post("/telegram/auth/phone", response_model=TelegramAuthResponse, status_code=202)
def start_phone_auth(payload: TelegramPhoneAuthStart, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_telegram_configuration()
    connection = TelegramConnection(user_id=current_user.id, phone_hint=f"***{payload.phone[-4:]}")
    db.add(connection)
    db.flush()
    auth_id = connection.id
    _create_auth_attempt(db, connection=connection, user_id=current_user.id, method="phone", initial={"state": "starting", "message": "Contacting Telegram", "phone": payload.phone})
    db.commit(); db.refresh(connection)
    client = _redis_client()
    client.hset(f"copy:telegram:auth:{auth_id}", mapping={"user_id": str(current_user.id), "connection_id": str(connection.id), "method": "phone", "state": "starting", "message": "Contacting Telegram"})
    client.expire(f"copy:telegram:auth:{auth_id}", 600)
    _publish_command("auth.phone.start", str(auth_id), {"auth_id": str(auth_id), "connection_id": str(connection.id), "phone": payload.phone}, f"auth:{auth_id}:start")
    return TelegramAuthResponse(auth_id=auth_id, method="phone", state="starting", message="Contacting Telegram")


@router.post("/telegram/auth/qr", response_model=TelegramAuthResponse, status_code=202)
def start_qr_auth(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_telegram_configuration()
    connection = TelegramConnection(user_id=current_user.id)
    db.add(connection)
    db.flush()
    auth_id = connection.id
    _create_auth_attempt(db, connection=connection, user_id=current_user.id, method="qr", initial={"state": "starting", "message": "Preparing QR code"})
    db.commit(); db.refresh(connection)
    client = _redis_client()
    client.hset(f"copy:telegram:auth:{auth_id}", mapping={"user_id": str(current_user.id), "connection_id": str(connection.id), "method": "qr", "state": "starting", "message": "Preparing QR code"})
    client.expire(f"copy:telegram:auth:{auth_id}", 600)
    _publish_command("auth.qr.start", str(auth_id), {"auth_id": str(auth_id), "connection_id": str(connection.id)}, f"auth:{auth_id}:start")
    return TelegramAuthResponse(auth_id=auth_id, method="qr", state="starting", message="Preparing QR code")


@router.get("/telegram/auth/{auth_id}", response_model=TelegramAuthResponse)
def get_auth_status(auth_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    data = _owned_auth(auth_id, current_user, db)
    return TelegramAuthResponse(auth_id=auth_id, method=data["method"], state=data["state"], qr_url=data.get("qr_url"), message=data.get("message", "Processing"))


@router.post("/telegram/auth/{auth_id}/code", response_model=TelegramAuthResponse, status_code=202)
def submit_auth_code(auth_id: uuid.UUID, payload: TelegramCodeSubmit, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    data = _owned_auth(auth_id, current_user, db)
    encrypted = SessionCipher(settings.ENCRYPTION_KEY).encrypt(payload.code)
    _publish_command("auth.phone.code", str(auth_id), {"auth_id": str(auth_id), "code_encrypted": encrypted}, f"auth:{auth_id}:code:{uuid_module.uuid4()}")
    return TelegramAuthResponse(auth_id=auth_id, method=data["method"], state="verifying", message="Verifying code")


@router.post("/telegram/auth/{auth_id}/password", response_model=TelegramAuthResponse, status_code=202)
def submit_auth_password(auth_id: uuid.UUID, payload: TelegramPasswordSubmit, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    data = _owned_auth(auth_id, current_user, db)
    encrypted = SessionCipher(settings.ENCRYPTION_KEY).encrypt(payload.password)
    _publish_command("auth.phone.password", str(auth_id), {"auth_id": str(auth_id), "password_encrypted": encrypted}, f"auth:{auth_id}:password:{uuid_module.uuid4()}")
    return TelegramAuthResponse(auth_id=auth_id, method=data["method"], state="verifying", message="Verifying two-step password")


@router.get("/telegram/connections", response_model=list[TelegramConnectionResponse])
def list_telegram_connections(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [TelegramConnectionResponse.model_validate(item) for item in repo.list_connections_for_user(db, user_id=current_user.id)]


@router.patch("/telegram/connections/{connection_id}", response_model=TelegramConnectionResponse)
def update_telegram_connection(connection_id: uuid.UUID, payload: CopyTradingSettingsUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    connection = repo.get_connection_for_user(db, connection_id=connection_id, user_id=current_user.id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found.")
    connection.is_paused = payload.is_paused
    db.commit(); db.refresh(connection)
    return TelegramConnectionResponse.model_validate(connection)


@router.delete("/telegram/connections/{connection_id}", status_code=204)
def disconnect_telegram(connection_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    connection = repo.get_connection_for_user(db, connection_id=connection_id, user_id=current_user.id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found.")
    _publish_command("connection.disconnect", str(uuid_module.uuid4()), {"connection_id": str(connection.id)}, f"disconnect:{connection.id}")
    db.delete(connection)
    db.commit()


@router.get("/telegram/connections/{connection_id}/dialogs", response_model=list[TelegramDialogResponse])
def list_telegram_dialogs(connection_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    connection = repo.get_connection_for_user(db, connection_id=connection_id, user_id=current_user.id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found.")
    if connection.state.value != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reconnect Telegram before searching channels and groups.",
        )
    dialogs = _request_live_dialogs(_redis_client(), connection_id)
    return [TelegramDialogResponse.model_validate(item) for item in dialogs]


@router.get("/sources", response_model=list[TelegramSourceResponse])
def list_sources(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [
        TelegramSourceResponse.model_validate(source)
        for source, _profile in repo.list_sources_for_user(
            db,
            user_id=current_user.id,
        )
    ]


@router.post("/sources", response_model=TelegramSourceResponse, status_code=201)
def create_source(payload: TelegramSourceCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if repo.get_connection_for_user(db, connection_id=payload.connection_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found.")
    source = TelegramSource(
        user_id=current_user.id,
        state=TelegramSourceState.ready,
        unsupported_reason=None,
        **payload.model_dump(),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return TelegramSourceResponse.model_validate(source)


@router.patch("/sources/{source_id}/pause", response_model=TelegramSourceResponse)
def update_source_pause(source_id: uuid.UUID, payload: CopyTradingSettingsUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = repo.get_source_for_user(db, source_id=source_id, user_id=current_user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Telegram source not found.")
    source.is_paused = payload.is_paused
    db.commit(); db.refresh(source)
    return TelegramSourceResponse.model_validate(source)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = repo.get_source_for_user(db, source_id=source_id, user_id=current_user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Telegram source not found.")
    routes = repo.list_routes_for_user(db, user_id=current_user.id)
    if any(r.source_id == source_id for r in routes):
        raise HTTPException(status_code=409, detail="Cannot delete a channel that still has copy routes. Remove the routes first.")
    db.delete(source)
    db.commit()


@router.get("/activity/{event_id}/raw")
def reveal_activity_raw(event_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    event = db.get(__import__("app.domains.copy_trading.models", fromlist=["CopyActivityEvent"]).CopyActivityEvent, event_id)
    if event is None or event.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Activity event not found.")
    if not event.encrypted_raw_message:
        return {"raw_message": None}
    return {"raw_message": SessionCipher(settings.ENCRYPTION_KEY).decrypt(event.encrypted_raw_message)}


@router.post("/emergency", status_code=202)
def emergency_action(payload: EmergencyActionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    correlation_id = str(uuid_module.uuid4())
    event = CopyEvent.new(stream=StreamName.execution_intents, event_type="emergency.execute", correlation_id=correlation_id, payload={"user_id": str(current_user.id), **payload.model_dump(mode="json", exclude={"confirmation"})}, idempotency_key=f"emergency:{correlation_id}")
    RedisStreamBus(_redis_client()).publish(event)
    service.record_activity(db, user_id=current_user.id, action="emergency.requested", title="Emergency action started", level=CopyActivityLevel.warning, correlation_id=correlation_id, parsed_details=event.payload)
    db.commit()
    return {"status": "processing", "correlation_id": correlation_id, "message": "Emergency action is processing."}

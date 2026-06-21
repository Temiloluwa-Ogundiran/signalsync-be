import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
import json
import redis
import uuid as uuid_module
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.copy_trading import service
from app.domains.copy_trading.schemas import (
    CopyAccountPolicyResponse,
    CopyAccountPolicyUpdate,
    CopyActivityResponse,
    CopyRouteCreate,
    CopyRouteResponse,
    CopyRouteUpdate,
    CopyTradingSettingsResponse,
    CopyTradingSettingsUpdate,
    EmergencyActionRequest,
    TelegramAuthResponse,
    TelegramCodeSubmit,
    TelegramConnectionResponse,
    TelegramDialogResponse,
    TelegramPasswordSubmit,
    TelegramPhoneAuthStart,
    TelegramSourceCreate,
    TelegramSourceResponse,
)
from app.core.config import settings
from app.domains.copy_trading import repository as repo
from app.domains.copy_trading.models import CopyActivityLevel, TelegramConnection, TelegramSource, TelegramSourceState
from app.domains.copy_trading.streams import CopyEvent, RedisStreamBus, StreamName
from app.domains.copy_trading.security import SessionCipher
from app.domains.users.models import User
from app.shared.deps import get_current_user


router = APIRouter(prefix="/copy-trading", tags=["copy-trading"])


def _redis_client():
    return redis.Redis.from_url(settings.COPY_TRADING_REDIS_URL, decode_responses=True)


def _publish_command(event_type: str, correlation_id: str, payload: dict, key: str) -> None:
    RedisStreamBus(_redis_client()).publish(CopyEvent.new(stream=StreamName.telegram_commands, event_type=event_type, correlation_id=correlation_id, payload=payload, idempotency_key=key))


def _require_telegram_configuration() -> None:
    if not settings.COPY_TRADING_ENABLED or not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        raise HTTPException(status_code=503, detail="Telegram connection is temporarily unavailable while service credentials are being configured.")


def _owned_auth(auth_id: uuid.UUID, current_user: User) -> dict:
    data = _redis_client().hgetall(f"copy:telegram:auth:{auth_id}")
    if not data or data.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=404, detail="Telegram sign-in was not found or has expired.")
    return data


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
    "/account-policies/{account_id}", response_model=CopyAccountPolicyResponse
)
def update_account_policy(
    account_id: uuid.UUID,
    payload: CopyAccountPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyAccountPolicyResponse:
    policy = service.update_account_policy(
        db,
        current_user=current_user,
        account_id=account_id,
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


@router.get("/activity", response_model=list[CopyActivityResponse])
def list_activity(
    limit: int = Query(default=50, ge=1, le=100),
    before: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CopyActivityResponse]:
    events = service.list_activity(
        db,
        current_user=current_user,
        limit=limit,
        before=before,
    )
    return [CopyActivityResponse.model_validate(event) for event in events]


@router.post("/telegram/auth/phone", response_model=TelegramAuthResponse, status_code=202)
def start_phone_auth(payload: TelegramPhoneAuthStart, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_telegram_configuration()
    connection = TelegramConnection(user_id=current_user.id, phone_hint=f"***{payload.phone[-4:]}")
    db.add(connection)
    db.commit()
    db.refresh(connection)
    auth_id = connection.id
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
    db.commit()
    db.refresh(connection)
    auth_id = connection.id
    client = _redis_client()
    client.hset(f"copy:telegram:auth:{auth_id}", mapping={"user_id": str(current_user.id), "connection_id": str(connection.id), "method": "qr", "state": "starting", "message": "Preparing QR code"})
    client.expire(f"copy:telegram:auth:{auth_id}", 600)
    _publish_command("auth.qr.start", str(auth_id), {"auth_id": str(auth_id), "connection_id": str(connection.id)}, f"auth:{auth_id}:start")
    return TelegramAuthResponse(auth_id=auth_id, method="qr", state="starting", message="Preparing QR code")


@router.get("/telegram/auth/{auth_id}", response_model=TelegramAuthResponse)
def get_auth_status(auth_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    data = _owned_auth(auth_id, current_user)
    return TelegramAuthResponse(auth_id=auth_id, method=data["method"], state=data["state"], qr_url=data.get("qr_url"), message=data.get("message", "Processing"))


@router.post("/telegram/auth/{auth_id}/code", response_model=TelegramAuthResponse, status_code=202)
def submit_auth_code(auth_id: uuid.UUID, payload: TelegramCodeSubmit, current_user: User = Depends(get_current_user)):
    data = _owned_auth(auth_id, current_user)
    _publish_command("auth.phone.code", str(auth_id), {"auth_id": str(auth_id), "code": payload.code}, f"auth:{auth_id}:code:{payload.code}")
    return TelegramAuthResponse(auth_id=auth_id, method=data["method"], state="verifying", message="Verifying code")


@router.post("/telegram/auth/{auth_id}/password", response_model=TelegramAuthResponse, status_code=202)
def submit_auth_password(auth_id: uuid.UUID, payload: TelegramPasswordSubmit, current_user: User = Depends(get_current_user)):
    data = _owned_auth(auth_id, current_user)
    _publish_command("auth.phone.password", str(auth_id), {"auth_id": str(auth_id), "password": payload.password}, f"auth:{auth_id}:password")
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
    if repo.get_connection_for_user(db, connection_id=connection_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found.")
    raw = _redis_client().get(f"copy:telegram:dialogs:{connection_id}")
    return [TelegramDialogResponse.model_validate(item) for item in json.loads(raw or "[]")]


@router.get("/sources", response_model=list[TelegramSourceResponse])
def list_sources(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = []
    for source, profile in repo.list_sources_for_user(db, user_id=current_user.id):
        data = TelegramSourceResponse.model_validate(source).model_dump()
        data["profile"] = profile
        result.append(TelegramSourceResponse.model_validate(data))
    return result


@router.post("/sources", response_model=TelegramSourceResponse, status_code=201)
def create_source(payload: TelegramSourceCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if repo.get_connection_for_user(db, connection_id=payload.connection_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Telegram connection not found.")
    source = TelegramSource(user_id=current_user.id, state=TelegramSourceState.learning, **payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    RedisStreamBus(_redis_client()).publish(CopyEvent.new(stream=StreamName.learning_jobs, event_type="source.learn", correlation_id=str(uuid_module.uuid4()), payload={"source_id": str(source.id)}, idempotency_key=f"learn:{source.id}"))
    return TelegramSourceResponse.model_validate(source)


@router.post("/sources/{source_id}/learn", response_model=TelegramSourceResponse, status_code=202)
def relearn_source(source_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = repo.get_source_for_user(db, source_id=source_id, user_id=current_user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Telegram source not found.")
    source.state = TelegramSourceState.learning
    db.commit()
    RedisStreamBus(_redis_client()).publish(CopyEvent.new(stream=StreamName.learning_jobs, event_type="source.learn", correlation_id=str(uuid_module.uuid4()), payload={"source_id": str(source.id)}, idempotency_key=f"learn:{source.id}:{int(datetime.now().timestamp())}"))
    return TelegramSourceResponse.model_validate(source)


@router.patch("/sources/{source_id}/pause", response_model=TelegramSourceResponse)
def update_source_pause(source_id: uuid.UUID, payload: CopyTradingSettingsUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = repo.get_source_for_user(db, source_id=source_id, user_id=current_user.id)
    if source is None:
        raise HTTPException(status_code=404, detail="Telegram source not found.")
    source.is_paused = payload.is_paused
    source.state = TelegramSourceState.paused if payload.is_paused else (TelegramSourceState.ready if source.profile_id else TelegramSourceState.learning)
    db.commit(); db.refresh(source)
    return TelegramSourceResponse.model_validate(source)


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

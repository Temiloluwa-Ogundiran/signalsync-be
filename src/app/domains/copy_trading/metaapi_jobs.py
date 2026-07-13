import asyncio
import logging
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.delivery import DeliveryResult
from app.domains.copy_trading.metaapi_client import (
    MetaApiProvisioningHttpClient,
    build_metaapi,
)
from app.domains.copy_trading.metaapi_connections import get_metaapi_runtime
from app.domains.copy_trading.metaapi_provisioning import (
    MetaApiProvisioningError,
    MetaApiProvisioningService,
)
from app.domains.copy_trading.models import (
    CopyTradingConnection,
    CopyTradingConnectionState,
)
from app.shared.utils.encryption import decrypt_secret


logger = logging.getLogger("copy-trading.provisioning")
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def _provisioning_lock_ttl_seconds() -> int:
    # Account deployment, broker connection, and streaming synchronization can
    # each consume the configured MetaApi timeout.
    return max(60, settings.METAAPI_CONNECTION_TIMEOUT_SECONDS * 3 + 30)


def _release_provisioning_lock(client, key: str, token: str) -> None:
    try:
        client.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
    except Exception:
        logger.exception("Could not release MetaApi provisioning lock key=%s", key)


def validate_terminal_account(connection, account_information: dict | None):
    if not account_information:
        return (
            CopyTradingConnectionState.synchronization_failed,
            "account_information_missing",
            "The broker account did not finish synchronizing.",
        )
    if str(account_information.get("login")) != str(connection.broker_login) or str(
        account_information.get("server", "")
    ).casefold() != str(connection.broker_server).casefold():
        return (
            CopyTradingConnectionState.synchronization_failed,
            "account_identity_mismatch",
            "The synchronized broker account does not match the requested login and server.",
        )
    if not account_information.get("tradeAllowed") or account_information.get(
        "investorMode"
    ):
        return (
            CopyTradingConnectionState.trading_disabled,
            "trading_disabled",
            "Trader access is required for copy trading.",
        )
    return None


async def _run_job(connection: CopyTradingConnection, *, delete: bool, db) -> bool:
    api = build_metaapi(settings.METAAPI_TOKEN, region=settings.METAAPI_REGION)
    control = MetaApiProvisioningHttpClient(
        token=settings.METAAPI_TOKEN,
        timeout_seconds=settings.METAAPI_CONNECTION_TIMEOUT_SECONDS,
    )
    provisioning = MetaApiProvisioningService(
        api=api,
        provisioning_client=control,
        region=settings.METAAPI_REGION,
        account_type=settings.METAAPI_ACCOUNT_TYPE,
        timeout_seconds=settings.METAAPI_CONNECTION_TIMEOUT_SECONDS,
    )

    def persist() -> None:
        db.commit()

    try:
        if delete:
            await provisioning.cleanup(connection, persist=persist)
            return True
        if not connection.encrypted_trader_password:
            return True
        return await provisioning.provision(
            connection,
            password=decrypt_secret(connection.encrypted_trader_password),
            persist=persist,
        )
    finally:
        await control.close()
        close = getattr(api, "close", None)
        if close is not None:
            close()
            await asyncio.sleep(0)


def provisioning_handler(event, redis_client) -> DeliveryResult:
    if not settings.METAAPI_TOKEN:
        return DeliveryResult.retry(
            "METAAPI_NOT_CONFIGURED", "MetaApi credentials are not configured."
        )
    connection_id = uuid.UUID(event.payload["connection_id"])
    lock_key = f"copy:metaapi:provisioning-lock:{connection_id}"
    lock_token = str(uuid.uuid4())
    acquired = redis_client.set(
        lock_key,
        lock_token,
        nx=True,
        ex=_provisioning_lock_ttl_seconds(),
    )
    if not acquired:
        return DeliveryResult.retry(
            "PROVISIONING_IN_PROGRESS",
            "MetaApi account provisioning is already in progress.",
        )

    try:
        with SessionLocal() as db:
            connection = db.get(CopyTradingConnection, connection_id)
            if connection is None:
                return DeliveryResult.success()
            try:
                runtime = get_metaapi_runtime()
                if event.event_type == "connection.delete" and connection.metaapi_account_id:
                    runtime.close_account(connection.metaapi_account_id)
                completed = asyncio.run(
                    _run_job(
                        connection,
                        delete=event.event_type == "connection.delete",
                        db=db,
                    )
                )
            except MetaApiProvisioningError as exc:
                if exc.retryable:
                    return DeliveryResult.retry(exc.code, exc.user_message)
                return DeliveryResult.success()
            if not completed:
                return DeliveryResult.retry(
                    "PROVISIONING_ACCEPTED",
                    "MetaApi is still provisioning the account.",
                )
            if event.event_type != "connection.delete" and connection.metaapi_account_id:
                streaming_connection = runtime.acquire(connection.metaapi_account_id)
                validation_error = validate_terminal_account(
                    connection,
                    streaming_connection.terminal_state.account_information,
                )
                if validation_error:
                    connection.state, connection.last_error_code, connection.last_error_message = (
                        validation_error
                    )
                    db.commit()
                    return DeliveryResult.success()
                connection.state = CopyTradingConnectionState.ready
                connection.last_health_at = datetime.now(timezone.utc)
                connection.last_error_code = None
                connection.last_error_message = None
                db.commit()
            return DeliveryResult.success()
    finally:
        _release_provisioning_lock(redis_client, lock_key, lock_token)

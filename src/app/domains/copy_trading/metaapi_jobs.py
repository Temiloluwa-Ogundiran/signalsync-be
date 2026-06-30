import asyncio
import uuid

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.copy_trading.delivery import DeliveryResult
from app.domains.copy_trading.metaapi_client import (
    MetaApiProvisioningHttpClient,
    build_metaapi,
)
from app.domains.copy_trading.metaapi_provisioning import (
    MetaApiProvisioningError,
    MetaApiProvisioningService,
)
from app.domains.copy_trading.models import CopyTradingConnection
from app.shared.utils.encryption import decrypt_secret


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


def provisioning_handler(event, _redis_client) -> DeliveryResult:
    if not settings.METAAPI_TOKEN:
        return DeliveryResult.retry(
            "METAAPI_NOT_CONFIGURED", "MetaApi credentials are not configured."
        )
    connection_id = uuid.UUID(event.payload["connection_id"])
    with SessionLocal() as db:
        connection = db.get(CopyTradingConnection, connection_id)
        if connection is None:
            return DeliveryResult.success()
        try:
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
        return DeliveryResult.success()

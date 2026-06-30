import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domains.copy_trading.models import (
    CopyTradingConnection,
    CopyTradingConnectionState,
)


PersistCallback = Callable[[], Any]


@dataclass(frozen=True)
class MetaApiProvisioningError(Exception):
    code: str
    state: CopyTradingConnectionState
    user_message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.user_message


def _detail_code(details: Any) -> str:
    if isinstance(details, dict):
        return str(details.get("code", ""))
    return str(details or "")


def classify_provisioning_error(
    payload: dict[str, Any] | None, *, status_code: int | None = None
) -> MetaApiProvisioningError:
    details = _detail_code((payload or {}).get("details")).upper()
    message = str((payload or {}).get("message", "")).upper()
    evidence = f"{details} {message}"
    if "E_SRV_NOT_FOUND" in evidence:
        return MetaApiProvisioningError(
            "server_not_found",
            CopyTradingConnectionState.server_not_found,
            "The broker server was not found. Check the exact MT5 server name.",
        )
    if "E_AUTH" in evidence or "INVALID ACCOUNT" in evidence:
        return MetaApiProvisioningError(
            "invalid_credentials",
            CopyTradingConnectionState.invalid_credentials,
            "The broker rejected the account login, server, or trader password.",
        )
    if "E_TRADING_ACCOUNT_DISABLED" in evidence or "ACCOUNT DISABLED" in evidence:
        return MetaApiProvisioningError(
            "trading_disabled",
            CopyTradingConnectionState.trading_disabled,
            "The broker reports that trading is disabled for this account.",
        )
    retryable = status_code is None or status_code >= 500 or status_code == 429
    return MetaApiProvisioningError(
        "provisioning_failed",
        CopyTradingConnectionState.provisioning_failed,
        "The copy account could not be provisioned. Please try again.",
        retryable=retryable,
    )


async def _persist(callback: PersistCallback) -> None:
    result = callback()
    if inspect.isawaitable(result):
        await result


class MetaApiProvisioningService:
    def __init__(
        self,
        *,
        api,
        provisioning_client,
        region: str,
        account_type: str,
        timeout_seconds: int,
    ) -> None:
        self._api = api
        self._provisioning_client = provisioning_client
        self._region = region
        self._account_type = account_type
        self._timeout_seconds = timeout_seconds

    async def provision(
        self,
        connection: CopyTradingConnection,
        *,
        password: str,
        persist: PersistCallback,
    ) -> bool:
        try:
            if not connection.metaapi_account_id:
                connection.state = CopyTradingConnectionState.provisioning
                connection.last_error_code = None
                connection.last_error_message = None
                await _persist(persist)
                result = await self._provisioning_client.create_account(
                    {
                        "login": str(connection.broker_login),
                        "password": password,
                        "name": connection.display_name,
                        "server": connection.broker_server,
                        "platform": connection.platform,
                        "magic": 0,
                        "manualTrades": False,
                        "type": self._account_type,
                        "region": self._region,
                        "reliability": "high",
                    },
                    connection.provisioning_transaction_id,
                )
                if result is None:
                    return False
                connection.metaapi_account_id = str(result["id"])

            connection.state = CopyTradingConnectionState.deploying
            await _persist(persist)
            account = await self._api.metatrader_account_api.get_account(
                connection.metaapi_account_id
            )
            if str(account.state).upper() != "DEPLOYED":
                await account.deploy()
                await account.wait_deployed(
                    timeout_in_seconds=self._timeout_seconds,
                    interval_in_milliseconds=500,
                )

            connection.state = CopyTradingConnectionState.connecting
            await _persist(persist)
            await account.wait_connected(
                timeout_in_seconds=self._timeout_seconds,
                interval_in_milliseconds=500,
            )
            connection.state = CopyTradingConnectionState.synchronizing
            await _persist(persist)
            return True
        except MetaApiProvisioningError as exc:
            connection.state = exc.state
            connection.last_error_code = exc.code
            connection.last_error_message = exc.user_message
            await _persist(persist)
            raise

    async def cleanup(
        self, connection: CopyTradingConnection, *, persist: PersistCallback
    ) -> None:
        connection.state = CopyTradingConnectionState.deleting
        await _persist(persist)
        if connection.metaapi_account_id:
            account = await self._api.metatrader_account_api.get_account(
                connection.metaapi_account_id
            )
            if str(account.state).upper() != "UNDEPLOYED":
                await account.undeploy()
            await account.remove()
        connection.encrypted_trader_password = None
        connection.state = CopyTradingConnectionState.deleted
        await _persist(persist)

import asyncio
from types import SimpleNamespace

import pytest

from app.domains.copy_trading.metaapi_provisioning import (
    MetaApiProvisioningError,
    MetaApiProvisioningService,
    classify_provisioning_error,
)
from app.domains.copy_trading.metaapi_jobs import validate_terminal_account
from app.domains.copy_trading.models import CopyTradingConnectionState
from tests.fakes.fake_metaapi import FakeMetaApi, FakeMetaApiAccount, FakeProvisioningClient


def connection() -> SimpleNamespace:
    return SimpleNamespace(
        id="connection-id",
        display_name="Primary copy account",
        broker_login="12345678",
        broker_server="Broker-MT5-Demo",
        platform="mt5",
        provisioning_transaction_id="a" * 32,
        metaapi_account_id=None,
        state=CopyTradingConnectionState.submitted,
        last_error_code=None,
        last_error_message=None,
    )


def test_provisioning_uses_cloud_g2_high_reliability_and_persisted_transaction() -> None:
    target = connection()
    control = FakeProvisioningClient([{"id": "meta-account", "state": "UNDEPLOYED"}])
    account = FakeMetaApiAccount("meta-account")
    states: list[CopyTradingConnectionState] = []

    async def persist() -> None:
        states.append(target.state)

    service = MetaApiProvisioningService(
        api=FakeMetaApi(account),
        provisioning_client=control,
        region="london",
        account_type="cloud-g2",
        timeout_seconds=120,
    )

    asyncio.run(service.provision(target, password="trader-password", persist=persist))

    payload, transaction_id = control.calls[0]
    assert payload == {
        "login": "12345678",
        "password": "trader-password",
        "name": "Primary copy account",
        "server": "Broker-MT5-Demo",
        "platform": "mt5",
        "magic": 0,
        "manualTrades": True,
        "type": "cloud-g2",
        "region": "london",
        "reliability": "high",
    }
    assert transaction_id == "a" * 32
    assert states == [
        CopyTradingConnectionState.provisioning,
        CopyTradingConnectionState.deploying,
        CopyTradingConnectionState.connecting,
        CopyTradingConnectionState.synchronizing,
    ]
    assert target.metaapi_account_id == "meta-account"
    assert account.deploy_calls == 1
    assert account.wait_deployed_calls == 1
    assert account.wait_connected_calls == 1


def test_accepted_provisioning_is_resumed_with_same_transaction_id() -> None:
    target = connection()
    control = FakeProvisioningClient([None, {"id": "meta-account", "state": "DEPLOYED"}])
    account = FakeMetaApiAccount("meta-account", state="DEPLOYED")
    service = MetaApiProvisioningService(
        api=FakeMetaApi(account),
        provisioning_client=control,
        region="london",
        account_type="cloud-g2",
        timeout_seconds=120,
    )

    assert asyncio.run(
        service.provision(target, password="password", persist=lambda: None)
    ) is False
    assert asyncio.run(
        service.provision(target, password="password", persist=lambda: None)
    ) is True
    assert [call[1] for call in control.calls] == ["a" * 32, "a" * 32]
    assert account.deploy_calls == 0


@pytest.mark.parametrize(
    ("details", "state", "code"),
    [
        ("E_AUTH", CopyTradingConnectionState.invalid_credentials, "invalid_credentials"),
        ({"code": "E_SRV_NOT_FOUND"}, CopyTradingConnectionState.server_not_found, "server_not_found"),
        ("E_TRADING_ACCOUNT_DISABLED", CopyTradingConnectionState.trading_disabled, "trading_disabled"),
    ],
)
def test_provisioning_errors_are_safe_and_actionable(details, state, code) -> None:
    error = classify_provisioning_error({"message": "raw provider details", "details": details})

    assert isinstance(error, MetaApiProvisioningError)
    assert error.state == state
    assert error.code == code
    assert "raw provider details" not in error.user_message


def test_cleanup_is_idempotent_and_removes_credentials() -> None:
    target = connection()
    target.metaapi_account_id = "meta-account"
    target.encrypted_trader_password = "encrypted"
    account = FakeMetaApiAccount("meta-account", state="DEPLOYED")
    service = MetaApiProvisioningService(
        api=FakeMetaApi(account),
        provisioning_client=FakeProvisioningClient([]),
        region="london",
        account_type="cloud-g2",
        timeout_seconds=120,
    )

    asyncio.run(service.cleanup(target, persist=lambda: None))

    assert account.undeploy_calls == 1
    assert account.remove_calls == 1
    assert target.encrypted_trader_password is None
    assert target.state == CopyTradingConnectionState.deleted


def test_terminal_identity_and_trader_permission_are_required_for_readiness() -> None:
    target = connection()
    target.broker_login = "12345678"
    target.broker_server = "Broker-MT5-Demo"

    assert validate_terminal_account(
        target,
        {
            "login": 12345678,
            "server": "Broker-MT5-Demo",
            "tradeAllowed": True,
            "investorMode": False,
        },
    ) is None
    assert validate_terminal_account(
        target,
        {
            "login": 12345678,
            "server": "Broker-MT5-Demo",
            "tradeAllowed": False,
            "investorMode": True,
        },
    ) == (
        CopyTradingConnectionState.trading_disabled,
        "trading_disabled",
        "Trader access is required for copy trading.",
    )

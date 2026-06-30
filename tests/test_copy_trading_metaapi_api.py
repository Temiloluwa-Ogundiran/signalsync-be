import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.domains.copy_trading.models import CopyTradingConnectionState
from app.domains.copy_trading.schemas import (
    CopyTradingConnectionCreate,
    CopyTradingConnectionResponse,
)
from app.main import app
from app.shared.deps import get_current_user


def test_connection_contract_accepts_trader_credentials_without_echoing_password() -> None:
    payload = CopyTradingConnectionCreate(
        display_name="Demo copy",
        broker_login="12345678",
        broker_server="Broker-MT5-Demo",
        trader_password="top-secret",
    )

    assert payload.broker_login == "12345678"
    assert payload.trader_password.get_secret_value() == "top-secret"
    assert "trader_password" not in CopyTradingConnectionResponse.model_fields
    assert "encrypted_trader_password" not in CopyTradingConnectionResponse.model_fields


def test_openapi_exposes_metaapi_connection_lifecycle() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/copy-trading/connections" in paths
    assert "/copy-trading/connections/{connection_id}" in paths
    assert "/copy-trading/connections/{connection_id}/retry" in paths


@patch("app.domains.copy_trading.router._publish_metaapi_command")
@patch("app.domains.copy_trading.router.service.create_copy_connection")
def test_create_connection_returns_accepted_and_queues_provisioning(
    create_copy_connection, publish
) -> None:
    user = MagicMock(id=uuid.uuid4())
    target = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user.id,
        display_name="Demo copy",
        broker_login="12345678",
        broker_server="Broker-MT5-Demo",
        platform="mt5",
        metaapi_account_id=None,
        state=CopyTradingConnectionState.submitted,
        last_error_code=None,
        last_error_message=None,
        symbol_catalog_refreshed_at=None,
        last_health_at=None,
        created_at="2026-06-30T12:00:00Z",
        updated_at="2026-06-30T12:00:00Z",
        provisioning_transaction_id="a" * 32,
    )
    create_copy_connection.return_value = target
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).post(
            "/copy-trading/connections",
            json={
                "display_name": "Demo copy",
                "broker_login": "12345678",
                "broker_server": "Broker-MT5-Demo",
                "trader_password": "top-secret",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert "password" not in response.text.lower()
    publish.assert_called_once_with(target, event_type="connection.provision")

from sqlalchemy import inspect

from app.core.config import Settings
from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopiedTrade,
    CopyRoute,
    CopyTradingConnection,
    CopyTradingConnectionState,
    SymbolMapping,
    TradeIntent,
)


def test_metaapi_settings_are_disabled_and_bounded_by_default() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://test:test@localhost/test",
        SECRET_KEY="test-secret",
        _env_file=None,
    )

    assert settings.METAAPI_TOKEN == ""
    assert settings.METAAPI_REGION == "london"
    assert settings.METAAPI_ACCOUNT_TYPE == "cloud-g2"
    assert settings.METAAPI_CONNECTION_TIMEOUT_SECONDS == 120
    assert settings.METAAPI_PROVISIONING_POLL_SECONDS == 60
    assert settings.METAAPI_PROVISIONING_MAX_ATTEMPTS == 15
    assert settings.METAAPI_IDLE_CONNECTION_SECONDS == 300
    assert settings.COPY_TRADING_METAAPI_ENABLED is False


def test_copy_trading_connection_declares_provisioning_and_health_fields() -> None:
    assert CopyTradingConnection.__tablename__ == "copy_trading_connections"
    columns = {column.name for column in inspect(CopyTradingConnection).columns}

    assert {
        "user_id",
        "display_name",
        "broker_login",
        "broker_server",
        "platform",
        "encrypted_trader_password",
        "metaapi_account_id",
        "provisioning_transaction_id",
        "state",
        "last_error_code",
        "last_error_message",
        "symbol_catalog_fingerprint",
        "symbol_catalog_refreshed_at",
        "last_health_at",
    } <= columns


def test_copy_trading_connection_state_machine_is_explicit() -> None:
    assert {state.value for state in CopyTradingConnectionState} == {
        "submitted",
        "provisioning",
        "deploying",
        "connecting",
        "synchronizing",
        "ready",
        "invalid_credentials",
        "server_not_found",
        "provisioning_failed",
        "broker_disconnected",
        "synchronization_failed",
        "trading_disabled",
        "deleting",
        "deleted",
    }


def test_copy_runtime_uses_connection_ids_instead_of_journal_account_ids() -> None:
    route_columns = {column.name for column in inspect(CopyRoute).columns}
    policy_columns = {column.name for column in inspect(CopyAccountPolicy).columns}
    intent_columns = {column.name for column in inspect(TradeIntent).columns}
    copied_columns = {column.name for column in inspect(CopiedTrade).columns}
    mapping_columns = {column.name for column in inspect(SymbolMapping).columns}

    assert "target_connection_id" in route_columns
    assert "target_account_id" not in route_columns
    assert "connection_id" in policy_columns
    assert "account_id" not in policy_columns
    assert "connection_id" in intent_columns
    assert "account_id" not in intent_columns
    assert "connection_id" in copied_columns
    assert "connection_id" in mapping_columns
    assert "account_id" not in mapping_columns


def test_activity_keeps_legacy_account_identity_separate_from_copy_connection() -> None:
    columns = {column.name for column in inspect(CopyActivityEvent).columns}

    assert "connection_id" in columns
    assert "legacy_account_id" in columns
    assert "account_id" not in columns

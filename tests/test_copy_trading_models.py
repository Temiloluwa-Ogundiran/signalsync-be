from sqlalchemy import inspect

from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyRoute,
    CopyRouteState,
    CopyTradingUserSettings,
    TelegramConnection,
    TelegramSource,
)


def test_copy_trading_tables_and_route_defaults_are_declared() -> None:
    assert {
        CopyTradingUserSettings.__tablename__,
        TelegramConnection.__tablename__,
        TelegramSource.__tablename__,
        CopyAccountPolicy.__tablename__,
        CopyRoute.__tablename__,
        CopyActivityEvent.__tablename__,
    } == {
        "copy_trading_user_settings",
        "telegram_connections",
        "telegram_sources",
        "copy_account_policies",
        "copy_routes",
        "copy_activity_events",
    }

    columns = {column.name for column in inspect(CopyRoute).columns}
    assert {"user_id", "source_id", "target_account_id", "magic_number"} <= columns
    assert {"fixed_lot", "take_profit_mode", "lot_distribution"} <= columns
    assert CopyRouteState.draft.value == "draft"
    assert CopyRouteState.paused.value == "paused"


def test_activity_response_storage_keeps_raw_message_encrypted() -> None:
    columns = {column.name for column in inspect(CopyActivityEvent).columns}
    assert "encrypted_raw_message" in columns
    assert "raw_message" not in columns

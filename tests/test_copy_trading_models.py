from sqlalchemy import inspect

from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyRoute,
    CopyRouteState,
    CopyTradingConnection,
    CopyTradingUserSettings,
    CopyDeadLetter,
    CopyWorkerHealth,
    CopyExecutionMetric,
    CopySignalReview,
    CopiedTrade,
    RouteSignalAssembly,
    SignalConversation,
    TelegramConnection,
    TelegramAuthAttempt,
    TelegramSource,
    TradeIntent,
)


def test_copy_trading_tables_and_route_defaults_are_declared() -> None:
    assert {
        CopyTradingUserSettings.__tablename__,
        CopyTradingConnection.__tablename__,
        TelegramConnection.__tablename__,
        TelegramSource.__tablename__,
        CopyAccountPolicy.__tablename__,
        CopyRoute.__tablename__,
        CopyActivityEvent.__tablename__,
    } == {
        "copy_trading_user_settings",
        "copy_trading_connections",
        "telegram_connections",
        "telegram_sources",
        "copy_account_policies",
        "copy_routes",
        "copy_activity_events",
    }

    columns = {column.name for column in inspect(CopyRoute).columns}
    assert {"user_id", "source_id", "target_connection_id", "magic_number"} <= columns
    assert {"fixed_lot", "take_profit_mode", "lot_distribution"} <= columns
    assert CopyRouteState.draft.value == "draft"
    assert CopyRouteState.paused.value == "paused"


def test_activity_response_storage_keeps_raw_message_encrypted() -> None:
    columns = {column.name for column in inspect(CopyActivityEvent).columns}
    assert "encrypted_raw_message" in columns
    assert "raw_message" not in columns


def test_reliability_runtime_tables_are_declared() -> None:
    assert {
        SignalConversation.__tablename__,
        RouteSignalAssembly.__tablename__,
        TelegramAuthAttempt.__tablename__,
        CopyDeadLetter.__tablename__,
        CopyWorkerHealth.__tablename__,
    } == {
        "signal_conversations",
        "route_signal_assemblies",
        "telegram_auth_attempts",
        "copy_dead_letters",
        "copy_worker_health",
    }


def test_production_control_tables_are_declared() -> None:
    assert CopyExecutionMetric.__tablename__ == "copy_execution_metrics"
    assert CopySignalReview.__tablename__ == "copy_signal_reviews"
    policy_columns = {column.name for column in inspect(CopyAccountPolicy).columns}
    route_columns = {column.name for column in inspect(CopyRoute).columns}
    assert {"max_spread_points", "max_slippage_points", "max_quote_age_seconds"} <= policy_columns
    assert "semantic_duplicate_window_seconds" in route_columns


def test_trade_intents_and_copied_trades_store_broker_identity_and_state() -> None:
    intent_columns = {column.name for column in inspect(TradeIntent).columns}
    copied_columns = {column.name for column in inspect(CopiedTrade).columns}

    assert "client_order_id" in intent_columns
    assert {"original_volume", "current_volume", "stop_loss", "take_profit"} <= copied_columns
    assert "broker_synced_at" in copied_columns

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in TradeIntent.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("client_order_id",) in unique_columns


def test_route_assembly_tracks_opening_generation() -> None:
    columns = {column.name for column in inspect(RouteSignalAssembly).columns}

    assert {
        "generation",
        "opening_action",
        "opening_intent_id",
        "terminal_reason",
        "completed_at",
    } <= columns


def test_route_assembly_generation_is_positive() -> None:
    checks = {
        constraint.name
        for constraint in RouteSignalAssembly.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert "ck_route_signal_assembly_generation_positive" in checks

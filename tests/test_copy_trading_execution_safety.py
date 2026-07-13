from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import pytest
from fastapi import HTTPException

from app.domains.copy_trading.execution import (
    calculate_signal_volume,
    catalog_fingerprint,
    ensure_account_risk_within_limits,
    ensure_exposure_within_limit,
    intent_legs_for_action,
    signal_volume_for_action,
)
from app.domains.copy_trading.engine import SignalAction
from app.domains.copy_trading.metaapi_execution import _mark_trade_terminal
from app.domains.copy_trading.models import (
    CopyTradingConnectionState,
    TelegramSourceState,
)
from app.domains.copy_trading.schemas import CopyRouteCreate
from app.domains.copy_trading.service import create_route


def connection(*, state: CopyTradingConnectionState) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        state=state,
    )


def test_fixed_each_counts_every_take_profit_leg() -> None:
    assert calculate_signal_volume(
        fixed_lot=Decimal("0.10"),
        take_profit_count=4,
        take_profit_mode="all",
        distribution="fixed_each",
    ) == Decimal("0.40")


def test_split_total_preserves_the_configured_total() -> None:
    assert calculate_signal_volume(
        fixed_lot=Decimal("0.10"),
        take_profit_count=4,
        take_profit_mode="all",
        distribution="split_total",
    ) == Decimal("0.10")


def test_existing_exposure_and_new_signal_must_fit_account_limit() -> None:
    with pytest.raises(ValueError, match="account maximum"):
        ensure_exposure_within_limit(
            current_exposure=Decimal("0.35"),
            signal_volume=Decimal("0.20"),
            maximum=Decimal("0.50"),
        )


def test_account_risk_accepts_trade_within_user_limits() -> None:
    ensure_account_risk_within_limits(
        symbol="EURUSDm",
        signal_volume=Decimal("0.10"),
        current_exposure=Decimal("0.20"),
        current_positions=2,
        equity=Decimal("9800"),
        daily_equity_anchor=Decimal("10000"),
        peak_equity=Decimal("10100"),
        max_lot_per_trade=Decimal("0.25"),
        max_total_lot=Decimal("1.00"),
        max_open_positions=5,
        daily_loss_limit=Decimal("500"),
        max_drawdown_percent=Decimal("5"),
        allowed_symbols=["EURUSD"],
        blocked_symbols=[],
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"signal_volume": Decimal("0.30")}, "per-trade maximum"),
        ({"current_exposure": Decimal("0.95")}, "total copied exposure"),
        ({"current_positions": 5}, "open-position maximum"),
        ({"symbol": "XAUUSDm"}, "allowed-symbol list"),
        ({"blocked_symbols": ["EURUSD"]}, "blocked"),
        ({"equity": Decimal("9400")}, "daily loss limit"),
        (
            {
                "equity": Decimal("9500"),
                "peak_equity": Decimal("10100"),
                "daily_loss_limit": Decimal("700"),
            },
            "drawdown limit",
        ),
    ],
)
def test_account_risk_rejects_each_user_limit(overrides, message) -> None:
    values = {
        "symbol": "EURUSDm",
        "signal_volume": Decimal("0.10"),
        "current_exposure": Decimal("0.20"),
        "current_positions": 2,
        "equity": Decimal("9800"),
        "daily_equity_anchor": Decimal("10000"),
        "peak_equity": Decimal("10100"),
        "max_lot_per_trade": Decimal("0.25"),
        "max_total_lot": Decimal("1.00"),
        "max_open_positions": 5,
        "daily_loss_limit": Decimal("500"),
        "max_drawdown_percent": Decimal("5"),
        "allowed_symbols": ["EURUSD"],
        "blocked_symbols": [],
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        ensure_account_risk_within_limits(**values)


def test_equity_based_limits_fail_closed_when_live_equity_is_unavailable() -> None:
    with pytest.raises(ValueError, match="equity is unavailable"):
        ensure_account_risk_within_limits(
            symbol="EURUSD",
            signal_volume=Decimal("0.10"),
            current_exposure=Decimal("0"),
            current_positions=0,
            equity=None,
            daily_equity_anchor=None,
            peak_equity=None,
            max_lot_per_trade=Decimal("1"),
            max_total_lot=Decimal("2"),
            max_open_positions=10,
            daily_loss_limit=Decimal("100"),
            max_drawdown_percent=None,
            allowed_symbols=[],
            blocked_symbols=[],
        )


@pytest.mark.parametrize(
    "action",
    [
        SignalAction.modify_sl_tp,
        SignalAction.break_even,
        SignalAction.partial_close,
        SignalAction.full_close,
        SignalAction.cancel_pending,
        SignalAction.status_only,
    ],
)
def test_management_actions_do_not_consume_new_exposure(action) -> None:
    assert signal_volume_for_action(
        action=action,
        fixed_lot=Decimal("0.10"),
        take_profit_count=4,
        take_profit_mode="all",
        distribution="fixed_each",
    ) == Decimal("0")


def test_terminal_management_action_clears_local_exposure() -> None:
    copied = SimpleNamespace(lifecycle_state="open", current_volume=Decimal("0.01"))

    _mark_trade_terminal(copied, SignalAction.full_close.value)

    assert copied.lifecycle_state == "closed"
    assert copied.current_volume == Decimal("0")


def test_additional_take_profit_counts_as_new_exposure() -> None:
    assert signal_volume_for_action(
        action=SignalAction.additional_tp,
        fixed_lot=Decimal("0.10"),
        take_profit_count=1,
        take_profit_mode="all",
        distribution="fixed_each",
    ) == Decimal("0.10")


def test_management_action_creates_exactly_one_intent_even_with_multiple_tps() -> None:
    legs = intent_legs_for_action(
        action=SignalAction.modify_sl_tp,
        fixed_lot=Decimal("0.10"),
        take_profits=[Decimal("1.10"), Decimal("1.20")],
        mode="all",
        distribution="fixed_each",
    )

    assert legs == [None]


def test_symbol_catalog_fingerprint_changes_with_trade_metadata() -> None:
    symbols = [{"name": "XAUUSDm", "trade_mode": 4, "contract_size": 100}]
    first = catalog_fingerprint(symbols)

    symbols[0]["trade_mode"] = 0

    assert catalog_fingerprint(symbols) != first


@patch("app.domains.copy_trading.service.repo")
def test_route_creation_rejects_unready_copy_connection(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.ready)
    target = connection(state=CopyTradingConnectionState.synchronizing)
    repo.get_source_for_user.return_value = source
    repo.get_copy_connection_for_user.return_value = target

    with pytest.raises(HTTPException) as error:
        create_route(
            MagicMock(),
            current_user=user,
            payload=CopyRouteCreate(
                source_id=source.id,
                target_connection_id=target.id,
                fixed_lot=Decimal("0.10"),
            ),
        )

    assert error.value.status_code == 409
    assert error.value.detail == "The copy account connection must be ready."

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import pytest
from fastapi import HTTPException

from app.domains.accounts.models import (
    ImportMethod,
    TradingAccountConnectionState,
    TradingPlatform,
)
from app.domains.copy_trading.execution import (
    calculate_signal_volume,
    catalog_fingerprint,
    ensure_exposure_within_limit,
    intent_legs_for_action,
    is_trade_ready,
    signal_volume_for_action,
)
from app.domains.copy_trading.engine import SignalAction
from app.domains.copy_trading.models import TelegramSourceState
from app.domains.copy_trading.schemas import CopyRouteCreate
from app.domains.copy_trading.service import create_route


def account(*, trader_password: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        platform=TradingPlatform.mt5,
        import_method=ImportMethod.auto_sync,
        connection_state=TradingAccountConnectionState.ready,
        is_archived=False,
        encrypted_trader_password=trader_password,
    )


def test_investor_only_account_is_sync_ready_but_not_trade_ready() -> None:
    assert is_trade_ready(account(trader_password=None)) is False
    assert is_trade_ready(account(trader_password="encrypted")) is True


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


@patch("app.domains.copy_trading.service.account_repo.get_account_by_id_for_user")
@patch("app.domains.copy_trading.service.repo")
def test_route_creation_rejects_investor_only_account(repo, get_account) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.ready)
    target = account(trader_password=None)
    repo.get_source_for_user.return_value = source
    get_account.return_value = target

    with pytest.raises(HTTPException) as error:
        create_route(
            MagicMock(),
            current_user=user,
            payload=CopyRouteCreate(
                source_id=source.id,
                target_account_id=target.id,
                fixed_lot=Decimal("0.10"),
            ),
        )

    assert error.value.status_code == 409
    assert error.value.detail == "Trader access is required for automatic copying."

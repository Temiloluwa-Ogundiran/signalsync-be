from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError

from app.domains.copy_trading.models import LotDistribution, MinimumFields, TakeProfitMode
from app.domains.copy_trading.schemas import (
    CopyAccountPolicyUpdate,
    CopyActivityResponse,
    CopyRouteCreate,
    CopyRouteUpdate,
    CopyTradingSettingsResponse,
)


SOURCE_ID = "11111111-1111-1111-1111-111111111111"
ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"


def test_split_total_requires_all_take_profits() -> None:
    with pytest.raises(ValidationError, match="Split lot is available only"):
        CopyRouteCreate(
            source_id=SOURCE_ID,
            target_connection_id=ACCOUNT_ID,
            fixed_lot=Decimal("0.10"),
            take_profit_mode=TakeProfitMode.lowest,
            lot_distribution=LotDistribution.split_total,
        )


def test_unsafe_minimum_requires_confirmation() -> None:
    with pytest.raises(ValidationError, match="confirmation"):
        CopyRouteCreate(
            source_id=SOURCE_ID,
            target_connection_id=ACCOUNT_ID,
            fixed_lot=Decimal("0.10"),
            minimum_fields=MinimumFields.direction_symbol,
            unsafe_minimum_confirmed=False,
        )


def test_route_update_rejects_assembly_window_above_ten_minutes() -> None:
    with pytest.raises(ValidationError):
        CopyRouteUpdate(assembly_window_seconds=601)


def test_route_update_preserves_omitted_fields() -> None:
    payload = CopyRouteUpdate(notify_success=False)
    assert payload.model_fields_set == {"notify_success"}


def test_production_controls_are_bounded() -> None:
    assert CopyRouteUpdate(semantic_duplicate_window_seconds=0).semantic_duplicate_window_seconds == 0
    with pytest.raises(ValidationError):
        CopyRouteUpdate(semantic_duplicate_window_seconds=3601)
    with pytest.raises(ValidationError):
        CopyAccountPolicyUpdate(high_spread_behavior="loop_forever")


def test_activity_response_never_exposes_encrypted_raw_message() -> None:
    assert "encrypted_raw_message" not in CopyActivityResponse.model_fields


def test_copy_trading_settings_response_matches_timestamp_free_table() -> None:
    settings = SimpleNamespace(user_id=uuid.uuid4(), is_paused=False)

    response = CopyTradingSettingsResponse.model_validate(settings)

    assert response.user_id == settings.user_id
    assert response.is_paused is False
    assert "created_at" not in response.model_dump()

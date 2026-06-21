import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401
from app.domains.accounts.models import (
    ImportMethod,
    TradingAccountConnectionState,
    TradingPlatform,
)
from app.domains.copy_trading.models import CopyRouteState, TelegramSourceState
from app.domains.copy_trading.schemas import CopyAccountPolicyUpdate, CopyRouteCreate
from app.domains.copy_trading.service import (
    create_route,
    delete_route,
    magic_number_for_route,
    pause_route,
    resume_route,
    update_account_policy,
)


def ready_account(user_id: uuid.UUID) -> MagicMock:
    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = user_id
    account.platform = TradingPlatform.mt5
    account.import_method = ImportMethod.auto_sync
    account.connection_state = TradingAccountConnectionState.ready
    account.is_archived = False
    return account


def route_payload(source_id: uuid.UUID, account_id: uuid.UUID, lot: str) -> CopyRouteCreate:
    return CopyRouteCreate(
        source_id=source_id,
        target_account_id=account_id,
        fixed_lot=Decimal(lot),
    )


def test_magic_number_is_stable_positive_and_signed_31_bit() -> None:
    route_id = uuid.UUID("ffffffff-1111-2222-3333-444444444444")
    first = magic_number_for_route(route_id)
    assert first == magic_number_for_route(route_id)
    assert 0 < first <= 0x7FFFFFFF


@patch("app.domains.copy_trading.service.account_repo.get_account_by_id_for_user")
@patch("app.domains.copy_trading.service.repo")
def test_create_route_rejects_lot_above_account_cap(repo, get_account) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.ready)
    account = ready_account(user.id)
    repo.get_source_for_user.return_value = source
    repo.get_route_by_source_and_account.return_value = None
    repo.get_account_policy.return_value = MagicMock(max_lot=Decimal("0.50"))
    get_account.return_value = account

    with pytest.raises(HTTPException) as exc:
        create_route(
            MagicMock(),
            current_user=user,
            payload=route_payload(source.id, account.id, "1.00"),
        )

    assert exc.value.status_code == 422
    repo.create_route.assert_not_called()


@patch("app.domains.copy_trading.service.account_repo.get_account_by_id_for_user")
@patch("app.domains.copy_trading.service.repo")
def test_create_ready_route_commits_with_activity(repo, get_account) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.ready)
    account = ready_account(user.id)
    policy = MagicMock(max_lot=Decimal("2.00"))
    repo.get_source_for_user.return_value = source
    repo.get_route_by_source_and_account.return_value = None
    repo.get_account_policy.return_value = policy
    get_account.return_value = account
    repo.create_route.side_effect = lambda _db, route: route
    db = MagicMock()

    route = create_route(
        db,
        current_user=user,
        payload=route_payload(source.id, account.id, "0.10"),
    )

    assert route.state == CopyRouteState.ready
    assert route.magic_number == magic_number_for_route(route.id)
    repo.create_activity.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(route)


@patch("app.domains.copy_trading.service.repo")
def test_pause_and_resume_preserve_previous_route_state(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    route = MagicMock(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        target_account_id=uuid.uuid4(),
        state=CopyRouteState.active,
        paused_from_state=None,
    )
    repo.get_route_for_user.return_value = route
    repo.get_or_create_user_settings.return_value.is_paused = False
    repo.get_account_policy.return_value.is_paused = False
    db = MagicMock()

    pause_route(db, current_user=user, route_id=route.id)
    assert route.state == CopyRouteState.paused
    assert route.paused_from_state == CopyRouteState.active

    resume_route(db, current_user=user, route_id=route.id)
    assert route.state == CopyRouteState.active
    assert route.paused_from_state is None


@patch("app.domains.copy_trading.service.account_repo.get_account_by_id_for_user")
@patch("app.domains.copy_trading.service.repo")
def test_account_cap_cannot_drop_below_existing_route_lot(repo, get_account) -> None:
    user = MagicMock(id=uuid.uuid4())
    account = ready_account(user.id)
    get_account.return_value = account
    repo.get_or_create_account_policy.return_value = MagicMock(max_lot=Decimal("5.00"))
    repo.max_fixed_lot_for_account.return_value = Decimal("2.00")

    with pytest.raises(HTTPException) as exc:
        update_account_policy(
            MagicMock(),
            current_user=user,
            account_id=account.id,
            payload=CopyAccountPolicyUpdate(max_lot=Decimal("1.00")),
        )

    assert exc.value.status_code == 409
    assert "2.00" in exc.value.detail


@patch("app.domains.copy_trading.service.repo")
def test_delete_route_rejects_active_route(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    route = MagicMock(id=uuid.uuid4(), state=CopyRouteState.active)
    repo.get_route_for_user.return_value = route
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        delete_route(db, current_user=user, route_id=route.id)

    assert exc.value.status_code == 409
    db.delete.assert_not_called()
    db.commit.assert_not_called()


@patch("app.domains.copy_trading.service.repo")
def test_delete_paused_route_records_activity_and_commits(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    route = MagicMock(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        target_account_id=uuid.uuid4(),
        state=CopyRouteState.paused,
    )
    repo.get_route_for_user.return_value = route
    db = MagicMock()

    delete_route(db, current_user=user, route_id=route.id)

    repo.create_activity.assert_called_once()
    db.delete.assert_called_once_with(route)
    db.commit.assert_called_once()

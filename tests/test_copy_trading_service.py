import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401
from app.domains.copy_trading.models import (
    CopyRouteState,
    CopyTradingConnectionState,
    TelegramSourceState,
)
from app.domains.copy_trading.schemas import CopyAccountPolicyUpdate, CopyRouteCreate
from app.domains.copy_trading.service import (
    activate_route,
    create_route,
    delete_route,
    magic_number_for_route,
    pause_route,
    resume_route,
    update_account_policy,
)


def ready_connection(user_id: uuid.UUID) -> MagicMock:
    connection = MagicMock()
    connection.id = uuid.uuid4()
    connection.user_id = user_id
    connection.state = CopyTradingConnectionState.ready
    return connection


def route_payload(
    source_id: uuid.UUID, connection_id: uuid.UUID, lot: str
) -> CopyRouteCreate:
    return CopyRouteCreate(
        source_id=source_id,
        target_connection_id=connection_id,
        fixed_lot=Decimal(lot),
    )


def test_magic_number_is_stable_positive_and_signed_31_bit() -> None:
    route_id = uuid.UUID("ffffffff-1111-2222-3333-444444444444")
    first = magic_number_for_route(route_id)
    assert first == magic_number_for_route(route_id)
    assert 0 < first <= 0x7FFFFFFF


@patch("app.domains.copy_trading.service.repo")
def test_create_route_rejects_lot_above_account_cap(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.ready)
    connection = ready_connection(user.id)
    repo.get_source_for_user.return_value = source
    repo.get_copy_connection_for_user.return_value = connection
    repo.get_route_by_source_and_connection.return_value = None
    repo.get_account_policy.return_value = MagicMock(max_lot=Decimal("0.50"))

    with pytest.raises(HTTPException) as exc:
        create_route(
            MagicMock(),
            current_user=user,
            payload=route_payload(source.id, connection.id, "1.00"),
        )

    assert exc.value.status_code == 422
    repo.create_route.assert_not_called()


@patch("app.domains.copy_trading.service.repo")
def test_create_ready_route_commits_with_activity(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.ready)
    connection = ready_connection(user.id)
    repo.get_source_for_user.return_value = source
    repo.get_copy_connection_for_user.return_value = connection
    repo.get_route_by_source_and_connection.return_value = None
    repo.get_account_policy.return_value = MagicMock(max_lot=Decimal("2.00"))
    repo.create_route.side_effect = lambda _db, route: route
    db = MagicMock()

    route = create_route(
        db,
        current_user=user,
        payload=route_payload(source.id, connection.id, "0.10"),
    )

    assert route.state == CopyRouteState.ready
    assert route.magic_number == magic_number_for_route(route.id)
    repo.create_activity.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(route)


@patch("app.domains.copy_trading.service.repo")
def test_create_route_does_not_depend_on_channel_analysis_state(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.unsupported)
    connection = ready_connection(user.id)
    repo.get_source_for_user.return_value = source
    repo.get_copy_connection_for_user.return_value = connection
    repo.get_route_by_source_and_connection.return_value = None
    repo.get_account_policy.return_value = MagicMock(max_lot=Decimal("2.00"))
    repo.create_route.side_effect = lambda _db, route: route

    route = create_route(
        MagicMock(),
        current_user=user,
        payload=route_payload(source.id, connection.id, "0.10"),
    )

    assert route.state == CopyRouteState.ready


@patch("app.domains.copy_trading.service.repo")
def test_activate_route_accepts_any_connected_source_state(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    route = MagicMock(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        target_connection_id=uuid.uuid4(),
        state=CopyRouteState.ready,
    )
    source = MagicMock(id=route.source_id, state=TelegramSourceState.unsupported)
    repo.get_route_for_user.return_value = route
    repo.get_source_for_user.return_value = source
    repo.get_copy_connection_for_user.return_value = ready_connection(user.id)
    db = MagicMock()

    result = activate_route(db, current_user=user, route_id=route.id)

    assert result.state == CopyRouteState.active
    assert source.state == TelegramSourceState.active
    db.commit.assert_called_once()


@patch("app.domains.copy_trading.service.repo")
def test_pause_and_resume_preserve_previous_route_state(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    route = MagicMock(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        target_connection_id=uuid.uuid4(),
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


@patch("app.domains.copy_trading.service.repo")
def test_account_cap_cannot_drop_below_existing_route_lot(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    connection = ready_connection(user.id)
    repo.get_copy_connection_for_user.return_value = connection
    repo.get_or_create_account_policy.return_value = MagicMock(max_lot=Decimal("5.00"))
    repo.max_fixed_lot_for_connection.return_value = Decimal("2.00")

    with pytest.raises(HTTPException) as exc:
        update_account_policy(
            MagicMock(),
            current_user=user,
            connection_id=connection.id,
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
        target_connection_id=uuid.uuid4(),
        state=CopyRouteState.paused,
    )
    repo.get_route_for_user.return_value = route
    db = MagicMock()

    delete_route(db, current_user=user, route_id=route.id)

    repo.create_activity.assert_called_once()
    db.delete.assert_called_once_with(route)
    db.commit.assert_called_once()

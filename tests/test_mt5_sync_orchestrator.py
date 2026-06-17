from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest
from starlette.requests import Request
from starlette.responses import Response

# Import domain models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.domains.accounts import repository as account_repo
from app.domains.accounts import router as accounts_router
from app.domains.accounts.models import SyncProvider, TradingAccountConnectionState
from app.domains.accounts.schemas import AccountResponse
from app.domains.accounts.mt5_core_client import Mt5CoreClientJobFailed, Mt5CoreClientRateLimited
from app.domains.accounts import service as account_service
from app.domains.journal import service as journal_service
from app.domains.users import repository as user_repo


@pytest.fixture
def db_session() -> MagicMock:
    return MagicMock()


def test_account_response_exposes_sync_state_fields() -> None:
    payload = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "meta_account_id": "acct-1",
        "broker_name": "Broker",
        "broker_login": "123456",
        "broker_server": "Demo-Server",
        "account_type": "live",
        "platform": "MT5",
        "currency": "USD",
        "timezone": "UTC",
        "broker_utc_offset": 0,
        "display_name": "Primary",
        "status": "synced",
        "connection_state": "ready",
        "is_data_ready_for_stats": True,
        "last_synced_at": datetime.now(timezone.utc),
        "last_bootstrap_synced_at": datetime.now(timezone.utc),
        "sync_error_message": None,
        "bootstrap_error_message": None,
        "last_sync_attempted_at": datetime.now(timezone.utc),
        "last_sync_outcome": "success",
        "next_sync_not_before": datetime.now(timezone.utc),
        "latest_balance": Decimal("10000.00"),
        "latest_equity": Decimal("10050.00"),
        "is_deleted": False,
        "sync_provider": "headless_mt5",
        "created_at": datetime.now(timezone.utc),
    }

    response = AccountResponse.model_validate(payload)

    assert response.last_sync_attempted_at == payload["last_sync_attempted_at"]
    assert response.next_sync_not_before == payload["next_sync_not_before"]
    assert response.latest_balance == Decimal("10000.00")
    assert response.latest_equity == Decimal("10050.00")
    assert response.sync_status.code == "ready"
    assert response.sync_status.severity == "success"
    assert response.sync_status.headline == "Connected"
    assert response.sync_status.detail == "Account is connected and ready."
    assert response.sync_status.action is None


def test_account_response_describes_empty_ready_account() -> None:
    payload = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "meta_account_id": "acct-empty",
        "broker_name": "Broker",
        "broker_login": "123456",
        "broker_server": "Demo-Server",
        "account_type": "live",
        "platform": "MT5",
        "currency": "USD",
        "timezone": "UTC",
        "broker_utc_offset": 0,
        "display_name": "Empty",
        "status": "synced",
        "connection_state": "ready",
        "is_data_ready_for_stats": True,
        "last_synced_at": datetime.now(timezone.utc),
        "last_bootstrap_synced_at": datetime.now(timezone.utc),
        "sync_error_message": None,
        "bootstrap_error_message": None,
        "last_sync_attempted_at": datetime.now(timezone.utc),
        "last_sync_outcome": "success_empty",
        "next_sync_not_before": None,
        "latest_balance": None,
        "latest_equity": None,
        "is_deleted": False,
        "sync_provider": "headless_mt5",
        "created_at": datetime.now(timezone.utc),
    }

    response = AccountResponse.model_validate(payload)

    assert response.sync_status.code == "ready_empty"
    assert response.sync_status.severity == "info"
    assert response.sync_status.headline == "Connected"
    assert response.sync_status.detail == "No closed trades found yet."
    assert response.sync_status.action == "Close a trade in MT5, then resync."


def test_account_response_describes_lifecycle_attention_states() -> None:
    base_payload = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "meta_account_id": "acct-state",
        "broker_name": "Broker",
        "broker_login": "123456",
        "broker_server": "Demo-Server",
        "account_type": "live",
        "platform": "MT5",
        "currency": "USD",
        "timezone": "UTC",
        "broker_utc_offset": 0,
        "display_name": "State",
        "status": "pending_sync",
        "connection_state": "pending_verification",
        "is_data_ready_for_stats": False,
        "last_synced_at": None,
        "last_bootstrap_synced_at": None,
        "sync_error_message": None,
        "bootstrap_error_message": None,
        "last_sync_attempted_at": None,
        "last_sync_outcome": None,
        "next_sync_not_before": None,
        "latest_balance": None,
        "latest_equity": None,
        "is_deleted": False,
        "sync_provider": "headless_mt5",
        "created_at": datetime.now(timezone.utc),
    }

    verifying = AccountResponse.model_validate(base_payload)
    assert verifying.sync_status.code == "pending_verification"
    assert verifying.sync_status.severity == "pending"

    failed = AccountResponse.model_validate(
        {
            **base_payload,
            "status": "error",
            "connection_state": "verification_failed",
            "sync_error_message": "MT5 authorization failed.",
        }
    )
    assert failed.sync_status.code == "verification_failed"
    assert failed.sync_status.severity == "error"
    assert failed.sync_status.detail == "MT5 authorization failed."

    warning = AccountResponse.model_validate(
        {
            **base_payload,
            "connection_state": "bootstrap_failed",
            "bootstrap_error_message": "History sync timed out.",
        }
    )
    assert warning.sync_status.code == "bootstrap_failed"
    assert warning.sync_status.severity == "warning"
    assert warning.sync_status.detail == "History sync timed out."


def test_touch_last_active_at_if_stale_returns_true_when_row_updated(
    db_session: MagicMock,
) -> None:
    execute_result = MagicMock()
    execute_result.rowcount = 1
    db_session.execute.return_value = execute_result

    updated = user_repo.touch_last_active_at_if_stale(
        db_session,
        user_id=uuid.uuid4(),
        observed_at=datetime.now(timezone.utc),
        min_interval_seconds=60,
    )

    assert updated is True
    db_session.execute.assert_called_once()


def test_create_account_defaults_to_headless_mt5(
    db_session: MagicMock,
) -> None:
    account = account_repo.create_account(
        db_session,
        user_id=uuid.uuid4(),
        meta_account_id="MT5:Server:12345",
        broker_name="Broker",
        broker_login="12345",
        broker_server="Server",
        encrypted_investor_password="encrypted",
        encrypted_trader_password=None,
        account_type="live",
        platform="MT5",
        currency="USD",
        timezone="UTC",
        broker_utc_offset=0,
        display_name="Primary",
    )

    assert account.sync_provider == SyncProvider.headless_mt5
    db_session.flush.assert_called_once()


def test_try_acquire_account_sync_lock_returns_database_result(
    db_session: MagicMock,
) -> None:
    db_session.execute.return_value.scalar.return_value = True

    acquired = account_repo.try_acquire_account_sync_lock(
        db_session,
        account_id=uuid.uuid4(),
    )

    assert acquired is True
    db_session.execute.assert_called_once()


@pytest.mark.anyio
async def test_auth_activity_middleware_touches_recent_user_activity() -> None:
    from app.shared.activity import AuthActivityMiddleware

    user_id = uuid.uuid4()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/accounts",
        "headers": [
            (b"authorization", f"Bearer test-token".encode("utf-8")),
        ],
    }
    request = Request(scope)
    response = Response(status_code=200)
    db_session = MagicMock()
    call_next = AsyncMock(return_value=response)

    session_context = MagicMock()
    session_context.__enter__.return_value = db_session
    session_context.__exit__.return_value = None

    with (
        patch("app.shared.activity.decode_token", return_value={"sub": str(user_id)}),
        patch("app.shared.activity.user_repo.touch_last_active_at_if_stale", return_value=True) as touch_mock,
        patch("app.shared.activity.SessionLocal", return_value=session_context),
    ):
        middleware = AuthActivityMiddleware(app=AsyncMock())
        result = await middleware.dispatch(request, call_next)

    assert result.status_code == 200
    touch_mock.assert_called_once()
    db_session.commit.assert_called_once()


@pytest.mark.anyio
async def test_auth_activity_middleware_skips_requests_without_bearer_token() -> None:
    from app.shared.activity import AuthActivityMiddleware

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/accounts",
        "headers": [],
    }
    request = Request(scope)
    response = Response(status_code=200)
    call_next = AsyncMock(return_value=response)

    with patch("app.shared.activity.user_repo.touch_last_active_at_if_stale") as touch_mock:
        middleware = AuthActivityMiddleware(app=AsyncMock())
        result = await middleware.dispatch(request, call_next)

    assert result.status_code == 200
    touch_mock.assert_not_called()


@pytest.mark.anyio
@patch("app.domains.accounts.sync_orchestrator.account_repo")
@patch("app.domains.accounts.sync_orchestrator.sync_account_deals_mt5")
async def test_orchestrate_mt5_sync_keeps_account_connected_when_rate_limited(
    mock_sync_account_deals_mt5,
    mock_account_repo,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync

    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = uuid.uuid4()
    account.connection_state = TradingAccountConnectionState.ready
    account.next_sync_not_before = None
    mock_account_repo.is_account_sync_locked.return_value = False
    mock_account_repo.list_recent_sync_attempts_for_user.return_value = []
    mock_sync_account_deals_mt5.side_effect = Mt5CoreClientRateLimited(
        "RATE_LIMITED",
        "Submission rate limit exceeded. Please retry shortly.",
        status_code=429,
        retry_after_seconds=60,
    )

    result = await orchestrate_mt5_sync(
        db_session,
        account=account,
        trigger="manual",
    )

    assert result.outcome == "rate_limited"
    assert result.retry_after_seconds == 60
    assert account.connection_state == TradingAccountConnectionState.ready
    mock_account_repo.mark_sync_retryable.assert_called_once()
    db_session.commit.assert_called_once()


@pytest.mark.anyio
@patch("app.domains.accounts.sync_orchestrator.account_repo")
@patch("app.domains.accounts.sync_orchestrator.sync_account_deals_mt5")
async def test_orchestrate_mt5_sync_marks_verification_failed_on_invalid_credentials(
    mock_sync_account_deals_mt5,
    mock_account_repo,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync

    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = uuid.uuid4()
    account.connection_state = TradingAccountConnectionState.ready
    account.next_sync_not_before = None
    mock_account_repo.is_account_sync_locked.return_value = False
    mock_account_repo.list_recent_sync_attempts_for_user.return_value = []
    mock_sync_account_deals_mt5.side_effect = Mt5CoreClientJobFailed(
        "Failed to login to MT5 account"
    )

    result = await orchestrate_mt5_sync(
        db_session,
        account=account,
        trigger="manual",
    )

    assert result.outcome == "invalid_credentials"
    assert "MT5 authorization failed" in (result.message or "")
    mock_account_repo.mark_account_verification_failed.assert_called_once()
    verification_args = mock_account_repo.mark_account_verification_failed.call_args.args
    assert verification_args[0] == db_session
    assert verification_args[1] == account
    assert "MT5 authorization failed" in verification_args[2]
    mock_account_repo.mark_sync_attention_required.assert_called_once()
    db_session.commit.assert_called_once()


@pytest.mark.anyio
@patch("app.domains.accounts.router.sync_account_task")
@patch("app.domains.accounts.router.account_repo")
@patch("app.domains.accounts.router.check_manual_sync_admission")
@patch("app.domains.accounts.router.account_service")
async def test_manual_sync_returns_admission_guard_without_enqueue(
    mock_account_service,
    mock_check_admission,
    mock_account_repo,
    mock_sync_account_task,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import Mt5SyncExecutionResult

    current_user = MagicMock(id=uuid.uuid4())
    account = MagicMock(id=uuid.uuid4(), sync_provider=SyncProvider.headless_mt5)
    mock_account_service.get_account.return_value = account
    mock_check_admission.return_value = Mt5SyncExecutionResult(
        outcome="rate_limited",
        retry_after_seconds=60,
        message="Submission rate limit exceeded. Please retry shortly.",
    )

    result = await accounts_router.manual_sync(
        account_id=account.id,
        db=db_session,
        current_user=current_user,
    )

    assert result["status"] == "rate_limited"
    assert result["retry_after_seconds"] == 60
    mock_sync_account_task.delay.assert_not_called()
    mock_account_repo.set_sync_attempt_started.assert_not_called()


@pytest.mark.anyio
@patch("app.domains.accounts.router.sync_account_task")
@patch("app.domains.accounts.router.account_repo")
@patch("app.domains.accounts.router.check_manual_sync_admission")
@patch("app.domains.accounts.router.account_service")
async def test_manual_sync_enqueues_when_admitted(
    mock_account_service,
    mock_check_admission,
    mock_account_repo,
    mock_sync_account_task,
    db_session: MagicMock,
) -> None:
    current_user = MagicMock(id=uuid.uuid4())
    account = MagicMock(id=uuid.uuid4(), sync_provider=SyncProvider.headless_mt5)
    mock_account_service.get_account.return_value = account
    mock_check_admission.return_value = None

    result = await accounts_router.manual_sync(
        account_id=account.id,
        db=db_session,
        current_user=current_user,
    )

    assert result["status"] == "queued"
    mock_account_repo.set_sync_attempt_started.assert_called_once()
    db_session.commit.assert_called_once()
    mock_sync_account_task.delay.assert_called_once_with(str(account.id))


@pytest.mark.anyio
@patch("app.domains.accounts.sync_orchestrator.account_repo")
async def test_orchestrate_mt5_sync_returns_in_progress_when_account_lock_is_active(
    mock_account_repo,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync

    mock_account_repo.is_account_sync_locked.return_value = True

    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = uuid.uuid4()
    account.next_sync_not_before = None

    result = await orchestrate_mt5_sync(
        db_session,
        account=account,
        trigger="manual",
    )

    assert result.outcome == "in_progress"
    assert result.retry_after_seconds == 10
    mock_account_repo.set_sync_attempt_started.assert_not_called()


@pytest.mark.anyio
@patch("app.domains.accounts.sync_orchestrator.account_repo")
async def test_orchestrate_mt5_sync_returns_cooldown_for_manual_sync_during_account_cooldown(
    mock_account_repo,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync

    mock_account_repo.is_account_sync_locked.return_value = False

    attempted_at = datetime(2026, 6, 7, 8, 0, tzinfo=timezone.utc)
    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = uuid.uuid4()
    account.next_sync_not_before = datetime(2026, 6, 7, 8, 4, tzinfo=timezone.utc)

    with patch(
        "app.domains.accounts.sync_orchestrator.datetime",
        wraps=datetime,
    ) as mock_datetime:
        mock_datetime.now.return_value = attempted_at
        result = await orchestrate_mt5_sync(
            db_session,
            account=account,
            trigger="manual",
        )

    assert result.outcome == "cooldown"
    assert result.retry_after_seconds == 240
    mock_account_repo.list_recent_sync_attempts_for_user.assert_not_called()
    mock_account_repo.set_sync_attempt_started.assert_not_called()


@pytest.mark.anyio
@patch("app.domains.accounts.sync_orchestrator.account_repo")
async def test_orchestrate_mt5_sync_returns_user_burst_rate_limit_before_mt5_call(
    mock_account_repo,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync

    mock_account_repo.is_account_sync_locked.return_value = False

    attempted_at = datetime(2026, 6, 7, 8, 0, 50, tzinfo=timezone.utc)
    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = uuid.uuid4()
    account.next_sync_not_before = None
    mock_account_repo.list_recent_sync_attempts_for_user.return_value = [
        datetime(2026, 6, 7, 8, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 7, 8, 0, 10, tzinfo=timezone.utc),
        datetime(2026, 6, 7, 8, 0, 20, tzinfo=timezone.utc),
        datetime(2026, 6, 7, 8, 0, 30, tzinfo=timezone.utc),
        datetime(2026, 6, 7, 8, 0, 40, tzinfo=timezone.utc),
    ]

    with patch(
        "app.domains.accounts.sync_orchestrator.datetime",
        wraps=datetime,
    ) as mock_datetime:
        mock_datetime.now.return_value = attempted_at
        result = await orchestrate_mt5_sync(
            db_session,
            account=account,
            trigger="manual",
        )

    assert result.outcome == "rate_limited"
    assert result.retry_after_seconds == 10
    mock_account_repo.set_sync_attempt_started.assert_not_called()


@pytest.mark.anyio
@patch("app.domains.accounts.sync_orchestrator.account_repo")
@patch("app.domains.accounts.sync_orchestrator.sync_account_deals_mt5")
async def test_orchestrate_mt5_sync_sets_manual_cooldown_after_success(
    mock_sync_account_deals_mt5,
    mock_account_repo,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import orchestrate_mt5_sync

    mock_account_repo.is_account_sync_locked.return_value = False

    attempted_at = datetime(2026, 6, 7, 8, 0, 0, tzinfo=timezone.utc)
    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = uuid.uuid4()
    account.next_sync_not_before = None
    mock_account_repo.list_recent_sync_attempts_for_user.return_value = []
    mock_sync_account_deals_mt5.return_value = SimpleNamespace(
        inserted_trades=1,
        touched_trading_dates=1,
    )

    with patch(
        "app.domains.accounts.sync_orchestrator.datetime",
        wraps=datetime,
    ) as mock_datetime:
        mock_datetime.now.return_value = attempted_at
        result = await orchestrate_mt5_sync(
            db_session,
            account=account,
            trigger="manual",
        )

    assert result.outcome == "success"
    mark_kwargs = mock_account_repo.mark_sync_success.call_args.kwargs
    assert mark_kwargs["next_sync_not_before"] == datetime(
        2026, 6, 7, 8, 5, 0, tzinfo=timezone.utc
    )


@patch("app.domains.accounts.service.account_repo")
def test_list_accounts_includes_latest_snapshot_balance(
    mock_account_repo,
    db_session: MagicMock,
) -> None:
    user = MagicMock(id=uuid.uuid4())
    account = MagicMock()
    account.id = uuid.uuid4()
    snapshot = MagicMock(balance=Decimal("10000.00"), equity=Decimal("10050.00"))
    mock_account_repo.list_accounts_for_user.return_value = [account]
    mock_account_repo.get_latest_snapshots_for_accounts.return_value = {
        account.id: snapshot,
    }

    accounts = account_service.list_accounts(db_session, current_user=user)

    assert accounts[0].latest_balance == Decimal("10000.00")
    assert accounts[0].latest_equity == Decimal("10050.00")


@patch("app.domains.journal.service._trades.journal_repo")
@patch("app.domains.journal.service._trades.account_repo")
def test_journal_trade_response_includes_backend_trading_date(
    mock_account_repo,
    mock_journal_repo,
    db_session: MagicMock,
) -> None:
    current_user = MagicMock(id=uuid.uuid4())
    account = MagicMock(id=uuid.uuid4(), timezone="UTC")
    trade = SimpleNamespace(
        id=uuid.uuid4(),
        account_id=account.id,
        broker_trade_id="100",
        symbol="EURUSD",
        direction="buy",
        open_price=Decimal("1.10"),
        close_price=Decimal("1.20"),
        volume=Decimal("0.10"),
        profit=Decimal("10.00"),
        commission=Decimal("0.00"),
        swap=Decimal("0.00"),
        net_profit=Decimal("10.00"),
        duration_seconds=3600,
        session="london",
        opened_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        closed_at=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc),
        is_manual=False,
        is_missed=False,
        balance_before_trade=None,
        net_roi_percent=None,
        created_at=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc),
        sl=None,
        tp=None,
        magic_number=None,
        position_id=None,
        trade_source=None,
        mfe=None,
        mae=None,
    )

    mock_account_repo.get_account_by_id_for_user.return_value = account
    mock_account_repo.list_trades_by_account.return_value = [trade]
    mock_journal_repo.map_trade_reviewed_at_by_trade_ids.return_value = {}
    mock_journal_repo.map_trade_journal_ratings_by_trade_ids.return_value = {}
    mock_journal_repo.map_trade_journal_assessments_by_trade_ids.return_value = {}

    items = journal_service.list_account_trades(
        db_session,
        current_user=current_user,
        account_id=account.id,
        from_date=None,
        to_date=None,
        limit=10,
    )

    assert items[0].trading_date.isoformat() == "2026-05-20"


@patch("app.domains.journal.service._analytics.journal_repo")
@patch("app.domains.journal.service._analytics._get_ready_accounts_for_user")
@patch("app.domains.journal.service._analytics._estimate_starting_balance")
def test_dashboard_recent_trades_works_for_all_accounts_without_single_account_context(
    mock_estimate_starting_balance,
    mock_get_ready_accounts_for_user,
    mock_journal_repo,
    db_session: MagicMock,
) -> None:
    account_one = MagicMock(id=uuid.uuid4(), timezone="Africa/Lagos")
    account_two = MagicMock(id=uuid.uuid4(), timezone="UTC")
    mock_get_ready_accounts_for_user.return_value = [account_one, account_two]
    mock_estimate_starting_balance.side_effect = [Decimal("1000.00"), Decimal("2000.00")]

    trade = SimpleNamespace(
        id=uuid.uuid4(),
        account_id=account_one.id,
        broker_trade_id="100",
        symbol="BTCUSD",
        direction="buy",
        open_price=Decimal("61346.05"),
        close_price=Decimal("61296.05"),
        volume=Decimal("0.50"),
        profit=Decimal("-10.33"),
        commission=Decimal("0.00"),
        swap=Decimal("0.00"),
        net_profit=Decimal("-10.33"),
        duration_seconds=1200,
        session="london",
        opened_at=datetime(2026, 6, 7, 1, 20, tzinfo=timezone.utc),
        closed_at=datetime(2026, 6, 7, 1, 40, tzinfo=timezone.utc),
        is_manual=False,
        is_missed=False,
        balance_before_trade=None,
        net_roi_percent=None,
        created_at=datetime(2026, 6, 7, 1, 40, tzinfo=timezone.utc),
        sl=None,
        tp=None,
        magic_number=None,
        position_id="1001",
        trade_source=None,
        mfe=None,
        mae=None,
    )

    mock_journal_repo.list_trade_rows_for_analytics.return_value = [trade]
    mock_journal_repo.list_recent_trades_for_dashboard.return_value = [trade]

    dashboard = journal_service.get_analytics_dashboard(
        db_session,
        account_id=None,
        user_id=uuid.uuid4(),
        from_date=None,
        to_date=None,
        recent_limit=8,
        time_basis="close",
        include_manual=True,
    )

    assert len(dashboard.recent_trades.items) == 1
    assert dashboard.recent_trades.items[0].symbol == "BTCUSD"
    assert dashboard.recent_trades.items[0].trading_date.isoformat() == "2026-06-07"


@pytest.mark.anyio
@patch("app.shared.utils.encryption.decrypt_secret", return_value="secret")
@patch("app.domains.accounts.mt5_core_client.Mt5CoreClient")
@patch("app.domains.journal.service._trades.account_repo")
async def test_list_account_open_positions_returns_live_mt5_snapshot(
    mock_account_repo,
    mock_mt5_client_cls,
    _mock_decrypt_secret,
    db_session: MagicMock,
) -> None:
    current_user = MagicMock(id=uuid.uuid4())
    account = MagicMock(
        id=uuid.uuid4(),
        broker_login="123456",
        broker_server="Demo-Server",
        broker_name="XM",
        encrypted_investor_password="encrypted",
        sync_provider=SyncProvider.headless_mt5,
    )
    mock_account_repo.get_account_by_id_for_user.return_value = account
    mock_mt5_client = mock_mt5_client_cls.return_value
    mock_mt5_client.get_open_positions = AsyncMock(
        return_value={
            "as_of": "2026-06-07T03:00:00Z",
            "positions": [
                {
                    "position_id": "1001",
                    "symbol": "BTCUSD",
                    "side": "buy",
                    "volume": 0.5,
                    "profit": -10.33,
                    "opened_at": "2026-06-07T01:20:00Z",
                    "price_open": 61346.05,
                    "price_current": 61296.05,
                    "sl": 0.0,
                    "tp": 0.0,
                    "magic": 123,
                    "comment": "demo",
                }
            ],
        }
    )

    result = await journal_service.list_account_open_positions(
        db_session,
        current_user=current_user,
        account_id=account.id,
        limit=10,
    )

    assert result.as_of.isoformat() == "2026-06-07T03:00:00+00:00"
    assert result.items[0].position_id == "1001"
    assert result.items[0].floating_profit == -10.33
    assert result.items[0].opened_at.isoformat() == "2026-06-07T01:20:00+00:00"

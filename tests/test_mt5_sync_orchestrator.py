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
from app.domains.accounts.mt5_core_client import Mt5CoreClientRateLimited
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
        "next_sync_not_before": datetime.now(timezone.utc),
        "last_sync_outcome": "success",
        "consecutive_sync_failures": 0,
        "latest_balance": Decimal("10000.00"),
        "latest_equity": Decimal("10050.00"),
        "is_deleted": False,
        "sync_provider": "headless_mt5",
        "created_at": datetime.now(timezone.utc),
    }

    response = AccountResponse.model_validate(payload)

    assert response.last_sync_attempted_at == payload["last_sync_attempted_at"]
    assert response.next_sync_not_before == payload["next_sync_not_before"]
    assert response.last_sync_outcome == "success"
    assert response.consecutive_sync_failures == 0
    assert response.latest_balance == Decimal("10000.00")
    assert response.latest_equity == Decimal("10050.00")


def test_mt5_recurring_sync_is_not_scheduled() -> None:
    from app.core.celery_app import celery_app

    assert "journal-sync-active-mt5-accounts" not in celery_app.conf.beat_schedule


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


def test_list_active_mt5_sync_candidates_returns_repo_rows(
    db_session: MagicMock,
) -> None:
    expected = [MagicMock(id=uuid.uuid4())]
    db_session.execute.return_value.scalars.return_value.all.return_value = expected

    rows = account_repo.list_active_mt5_sync_candidates(
        db_session,
        active_after=datetime.now(timezone.utc),
        now=datetime.now(timezone.utc),
    )

    assert rows == expected
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
    account.connection_state = TradingAccountConnectionState.ready
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


@patch("app.tasks.journal_sync_tasks.account_repo")
@patch("app.tasks.journal_sync_tasks.orchestrate_mt5_sync")
@patch("app.tasks.journal_sync_tasks.SessionLocal")
def test_sync_all_mt5_accounts_only_runs_for_recently_active_users(
    mock_session_local,
    mock_orchestrate_mt5_sync,
    mock_account_repo,
) -> None:
    from app.domains.accounts.sync_orchestrator import Mt5SyncExecutionResult
    from app.tasks.journal_sync_tasks import sync_all_mt5_accounts

    active_account = MagicMock(id=uuid.uuid4())
    db_for_candidates = MagicMock()
    db_for_candidates.__enter__.return_value = db_for_candidates
    db_for_candidates.__exit__.return_value = None
    db_for_account = MagicMock()
    db_for_account.__enter__.return_value = db_for_account
    db_for_account.__exit__.return_value = None
    mock_session_local.side_effect = [db_for_candidates, db_for_account]

    mock_account_repo.list_active_mt5_sync_candidates.return_value = [active_account]
    mock_account_repo.get_account_by_id.return_value = active_account
    mock_orchestrate_mt5_sync.return_value = Mt5SyncExecutionResult(outcome="success")

    result = sync_all_mt5_accounts()

    assert result["triggered"] == 1
    mock_account_repo.list_active_mt5_sync_candidates.assert_called_once()


@pytest.mark.anyio
@patch("app.domains.accounts.router.account_service")
@patch("app.domains.accounts.router.orchestrate_mt5_sync")
async def test_manual_sync_uses_shared_orchestrator(
    mock_orchestrate_mt5_sync,
    mock_account_service,
    db_session: MagicMock,
) -> None:
    from app.domains.accounts.sync_orchestrator import Mt5SyncExecutionResult

    current_user = MagicMock(id=uuid.uuid4())
    account = MagicMock()
    account.id = uuid.uuid4()
    account.sync_provider = "headless_mt5"
    mock_account_service.get_account.return_value = account
    mock_orchestrate_mt5_sync.return_value = Mt5SyncExecutionResult(
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
    mock_orchestrate_mt5_sync.assert_called_once()


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


@patch("app.domains.journal.service.journal_repo")
@patch("app.domains.journal.service.account_repo")
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


@patch("app.domains.journal.service.journal_repo")
@patch("app.domains.journal.service._get_ready_accounts_for_user")
@patch("app.domains.journal.service._estimate_starting_balance")
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

    mock_journal_repo.list_trades_filtered_multi.return_value = [trade]

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
@patch("app.domains.journal.service.account_repo")
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

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Import the journal models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.core.config import settings
from app.domains.accounts.models import (
    TradingAccount,
    TradingAccountConnectionState,
    TradingAccountType,
    TradingPlatform,
)
from app.domains.accounts.mt5_core_client import (
    Mt5CoreClientJobFailed,
)
from app.domains.accounts.schemas import AccountConnectRequest, TraderAccessRequest
from app.domains.accounts.service import connect_account, enable_trader_access
from app.domains.accounts.sync import SyncResult
from app.tasks.journal_sync_tasks import bootstrap_account


@pytest.fixture
def db_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def current_user() -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    return user


@pytest.fixture
def payload() -> AccountConnectRequest:
    return AccountConnectRequest(
        broker_name="Test Broker",
        broker_login="123456",
        broker_server="BrokerServer",
        investor_password="secure_investor_password",
        trader_password=None,
        account_type=TradingAccountType.live,
        platform=TradingPlatform.mt5,
        currency="USD",
        timezone="UTC",
        broker_utc_offset=0,
        display_name="Live MT5",
    )


@pytest.fixture
def mock_account() -> TradingAccount:
    acct = TradingAccount()
    acct.id = uuid.uuid4()
    acct.broker_login = "123456"
    acct.broker_server = "BrokerServer"
    acct.broker_name = "Test Broker"
    acct.encrypted_investor_password = "encrypted_password"
    acct.connection_state = TradingAccountConnectionState.pending_verification
    return acct


@pytest.mark.anyio
@patch("app.tasks.journal_sync_tasks.bootstrap_account.delay")
@patch("app.domains.accounts.service.Mt5CoreClient")
@patch("app.domains.accounts.service.account_repo")
@patch("app.domains.accounts.service.encrypt_secret")
async def test_connect_account_returns_pending_account_before_mt5_verification(
    mock_encrypt_secret,
    mock_repo,
    mock_client_cls,
    mock_bootstrap_delay,
    db_session,
    current_user,
    payload,
    mock_account,
) -> None:
    mock_repo.resolve_mt5_server_name.return_value = payload.broker_server
    mock_repo.get_account_by_user_and_meta_id.return_value = None
    mock_repo.create_account.return_value = mock_account
    mock_encrypt_secret.return_value = "encrypted_password"
    result = await connect_account(db_session, current_user=current_user, payload=payload)

    assert result == mock_account
    mock_client_cls.assert_not_called()
    mock_repo.create_account.assert_called_once()
    assert mock_repo.create_account.call_args.kwargs["id"] is not None
    db_session.commit.assert_called_once()
    db_session.refresh.assert_called_once_with(mock_account)
    mock_bootstrap_delay.assert_called_once_with(str(mock_account.id))


@pytest.mark.anyio
@patch("app.domains.accounts.service.Mt5CoreClient")
@patch("app.domains.accounts.service.account_repo")
@patch("app.domains.accounts.service.encrypt_secret")
async def test_enable_trader_access_requires_mt5_trade_permission(
    mock_encrypt_secret,
    mock_repo,
    mock_client_cls,
    db_session,
    current_user,
    mock_account,
) -> None:
    mock_account.connection_state = TradingAccountConnectionState.ready
    mock_account.is_archived = False
    mock_account.import_method = "auto_sync"
    mock_repo.get_account_by_id_for_user.return_value = mock_account
    mock_client_cls.return_value.verify_credentials = AsyncMock(
        return_value={
            "verified": True,
            "login": int(mock_account.broker_login),
            "server": mock_account.broker_server,
            "trade_allowed": False,
        }
    )

    with pytest.raises(HTTPException) as exc:
        await enable_trader_access(
            db_session,
            current_user=current_user,
            account_id=mock_account.id,
            payload=TraderAccessRequest(trader_password="investor-password"),
        )

    assert exc.value.status_code == 400
    assert "full trading password" in exc.value.detail.lower()
    mock_encrypt_secret.assert_not_called()
    db_session.commit.assert_not_called()


@pytest.mark.anyio
@patch("app.domains.accounts.service.Mt5CoreClient")
@patch("app.domains.accounts.service.account_repo")
@patch("app.domains.accounts.service.encrypt_secret", return_value="encrypted-trader")
async def test_enable_trader_access_stores_verified_trader_password(
    mock_encrypt_secret,
    mock_repo,
    mock_client_cls,
    db_session,
    current_user,
    mock_account,
) -> None:
    mock_account.connection_state = TradingAccountConnectionState.ready
    mock_account.is_archived = False
    mock_account.import_method = "auto_sync"
    mock_repo.get_account_by_id_for_user.return_value = mock_account
    mock_client_cls.return_value.verify_credentials = AsyncMock(
        return_value={
            "verified": True,
            "login": int(mock_account.broker_login),
            "server": mock_account.broker_server,
            "trade_allowed": True,
        }
    )

    result = await enable_trader_access(
        db_session,
        current_user=current_user,
        account_id=mock_account.id,
        payload=TraderAccessRequest(trader_password="real-password"),
    )

    assert result is mock_account
    assert mock_account.encrypted_trader_password == "encrypted-trader"
    mock_encrypt_secret.assert_called_once_with("real-password")
    db_session.commit.assert_called_once()


@pytest.mark.anyio
@patch("app.tasks.journal_sync_tasks.bootstrap_account.delay")
@patch("app.domains.accounts.service.account_repo")
async def test_connect_account_existing_active_account_still_conflicts(
    mock_repo,
    mock_bootstrap_delay,
    db_session,
    current_user,
    payload,
    mock_account,
) -> None:
    mock_account.is_archived = False
    mock_repo.get_account_by_user_and_meta_id.return_value = mock_account

    with pytest.raises(HTTPException) as exc:
        await connect_account(db_session, current_user=current_user, payload=payload)

    assert exc.value.status_code == 409
    mock_bootstrap_delay.assert_not_called()


@patch("app.tasks.journal_sync_tasks.sync_account_deals_mt5", new_callable=AsyncMock)
@patch("app.tasks.journal_sync_tasks.ingest_mt5_snapshots")
@patch("app.tasks.journal_sync_tasks.Mt5CoreClient")
@patch("app.tasks.journal_sync_tasks.decrypt_secret")
@patch("app.tasks.journal_sync_tasks.account_repo")
@patch("app.tasks.journal_sync_tasks.SessionLocal")
def test_bootstrap_account_verifies_snapshots_and_syncs_history(
    mock_session_local,
    mock_repo,
    mock_decrypt_secret,
    mock_client_cls,
    mock_ingest_snapshots,
    mock_sync_account_deals_mt5,
    mock_account,
) -> None:
    db = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = db
    context.__exit__.return_value = None
    mock_session_local.return_value = context
    mock_repo.get_account_by_id.return_value = mock_account
    mock_decrypt_secret.return_value = "investor-password"

    mock_client = AsyncMock()
    mock_client.verify_credentials.return_value = {
        "verified": True,
        "login": int(mock_account.broker_login),
        "server": mock_account.broker_server,
        "balance": 5000.0,
        "equity": 5100.0,
    }
    mock_client_cls.return_value = mock_client
    mock_sync_account_deals_mt5.return_value = SyncResult(
        inserted_trades=2,
        touched_trading_dates=1,
    )

    result = bootstrap_account.run(str(mock_account.id))

    assert result["status"] == "ready"
    assert result["inserted_trades"] == 2
    mock_ingest_snapshots.assert_called_once()
    mock_repo.mark_account_bootstrapping.assert_called_once_with(db, mock_account)
    mock_repo.mark_account_ready_for_stats.assert_called_once()
    assert db.commit.call_count == 2


@patch("app.tasks.journal_sync_tasks.sync_account_deals_mt5", new_callable=AsyncMock)
@patch("app.tasks.journal_sync_tasks.Mt5CoreClient")
@patch("app.tasks.journal_sync_tasks.decrypt_secret")
@patch("app.tasks.journal_sync_tasks.account_repo")
@patch("app.tasks.journal_sync_tasks.SessionLocal")
def test_bootstrap_account_skips_verify_when_already_bootstrapping(
    mock_session_local,
    mock_repo,
    mock_decrypt_secret,
    mock_client_cls,
    mock_sync_account_deals_mt5,
    mock_account,
) -> None:
    mock_account.connection_state = TradingAccountConnectionState.bootstrapping
    db = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = db
    context.__exit__.return_value = None
    mock_session_local.return_value = context
    mock_repo.get_account_by_id.return_value = mock_account
    mock_decrypt_secret.return_value = "investor-password"
    mock_sync_account_deals_mt5.return_value = SyncResult(
        inserted_trades=0,
        touched_trading_dates=0,
    )

    result = bootstrap_account.run(str(mock_account.id))

    assert result["status"] == "ready"
    verify_client = mock_client_cls.return_value
    verify_client.verify_credentials.assert_not_called()
    mock_sync_account_deals_mt5.assert_awaited_once()


@patch("app.tasks.journal_sync_tasks.Mt5CoreClient")
@patch("app.tasks.journal_sync_tasks.decrypt_secret")
@patch("app.tasks.journal_sync_tasks.account_repo")
@patch("app.tasks.journal_sync_tasks.SessionLocal")
def test_bootstrap_account_marks_invalid_credentials(
    mock_session_local,
    mock_repo,
    mock_decrypt_secret,
    mock_client_cls,
    mock_account,
) -> None:
    db = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = db
    context.__exit__.return_value = None
    mock_session_local.return_value = context
    mock_repo.get_account_by_id.return_value = mock_account
    mock_decrypt_secret.return_value = "investor-password"

    mock_client = AsyncMock()
    mock_client.verify_credentials.side_effect = Mt5CoreClientJobFailed("Invalid account")
    mock_client_cls.return_value = mock_client

    result = bootstrap_account.run(str(mock_account.id))

    assert result["status"] == "verification_failed"
    mock_repo.mark_account_verification_failed.assert_called_once()
    args = mock_repo.mark_account_verification_failed.call_args.args
    assert args[0] == db
    assert args[1] == mock_account
    assert "MT5 authorization failed" in args[2]
    db.commit.assert_called_once()

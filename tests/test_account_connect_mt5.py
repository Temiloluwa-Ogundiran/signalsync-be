import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import HTTPException

# Import the journal models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.domains.accounts.models import TradingAccount, TradingAccountConnectionState, TradingAccountStatus, TradingAccountType, TradingPlatform
from app.domains.accounts.schemas import AccountConnectRequest
from app.domains.accounts.service import connect_account
from app.domains.accounts.mt5_core_client import (
    Mt5CoreClientError,
    Mt5CoreClientJobFailed,
    Mt5CoreClientRateLimited,
    Mt5CoreClientTimeout,
)

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
@patch("app.domains.accounts.service.account_repo")
@patch("app.domains.accounts.service.encrypt_secret")
@patch("app.domains.accounts.mt5_core_client.Mt5CoreClient")
async def test_connect_account_invalid_credentials(
    mock_client_cls,
    mock_encrypt_secret,
    mock_repo,
    db_session,
    current_user,
    payload
) -> None:
    # Set up client verification failure
    mock_client = AsyncMock()
    mock_client.verify_credentials.side_effect = Mt5CoreClientJobFailed("Invalid credentials")
    mock_client_cls.return_value = mock_client

    mock_repo.get_account_by_user_and_meta_id.return_value = None

    # Verification failure should raise 400 Bad Request
    with pytest.raises(HTTPException) as exc:
        await connect_account(db_session, current_user=current_user, payload=payload)
    
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_CREDENTIALS"
    assert "Credential verification failed" in exc.value.detail["message"]
    
    # DB create_account should never be called (persistence boundary)
    mock_repo.create_account.assert_not_called()
    db_session.commit.assert_not_called()


@pytest.mark.anyio
@patch("app.domains.accounts.service.account_repo")
@patch("app.domains.accounts.service.encrypt_secret")
@patch("app.domains.accounts.mt5_core_client.Mt5CoreClient")
@patch("app.domains.accounts.service.ingest_mt5_core_history_result")
async def test_connect_account_success(
    mock_ingest,
    mock_client_cls,
    mock_encrypt_secret,
    mock_repo,
    db_session,
    current_user,
    payload,
    mock_account
) -> None:
    mock_client = AsyncMock()
    mock_client.verify_credentials.return_value = {"verified": True}
    mock_client.submit_history_sync.return_value = {"deals": [{"ticket": "1"}], "broker_offset_seconds": 0}
    mock_client_cls.return_value = mock_client

    mock_repo.get_account_by_user_and_meta_id.return_value = None
    mock_repo.create_account.return_value = mock_account
    mock_encrypt_secret.return_value = "encrypted_password"

    result = await connect_account(db_session, current_user=current_user, payload=payload)

    # Asserts
    assert result == mock_account
    mock_repo.create_account.assert_called_once()
    mock_repo.mark_account_bootstrapping.assert_called_once_with(db_session, mock_account)
    mock_ingest.assert_called_once_with(db_session, account=mock_account, result={"deals": [{"ticket": "1"}], "broker_offset_seconds": 0})
    mock_repo.mark_account_ready_for_stats.assert_called_once()


@pytest.mark.anyio
@patch("app.domains.accounts.service.account_repo")
@patch("app.domains.accounts.service.encrypt_secret")
@patch("app.domains.accounts.mt5_core_client.Mt5CoreClient")
async def test_connect_account_warning_state_recovery(
    mock_client_cls,
    mock_encrypt_secret,
    mock_repo,
    db_session,
    current_user,
    payload,
    mock_account
) -> None:
    mock_client = AsyncMock()
    mock_client.verify_credentials.return_value = {"verified": True}
    # Submit sync fails
    mock_client.submit_history_sync.side_effect = Mt5CoreClientTimeout("Sync timed out")
    mock_client_cls.return_value = mock_client

    mock_repo.get_account_by_user_and_meta_id.return_value = None
    mock_repo.create_account.return_value = mock_account
    mock_encrypt_secret.return_value = "encrypted_password"

    # Sync timeout should NOT raise HTTPException, instead mark bootstrap_failed
    result = await connect_account(db_session, current_user=current_user, payload=payload)

    assert result == mock_account
    mock_repo.create_account.assert_called_once()
    mock_repo.mark_account_bootstrapping.assert_called_once_with(db_session, mock_account)
    
    # Check that bootstrap failed and error message are set, and commit is still called
    mock_repo.mark_account_bootstrap_failed.assert_called_once_with(db_session, mock_account, "Sync timed out")
    mock_repo.set_account_sync_error.assert_called_once_with(db_session, mock_account, "Initial sync failed: Sync timed out")


@pytest.mark.anyio
@patch("app.domains.accounts.service.account_repo")
@patch("app.domains.accounts.mt5_core_client.Mt5CoreClient")
async def test_connect_account_rate_limited_uses_429_response(
    mock_client_cls,
    mock_repo,
    db_session,
    current_user,
    payload,
) -> None:
    mock_client = AsyncMock()
    mock_client.verify_credentials.side_effect = Mt5CoreClientRateLimited(
        "RATE_LIMITED",
        "Submission rate limit exceeded. Please retry shortly.",
        status_code=429,
        retry_after_seconds=60,
    )
    mock_client_cls.return_value = mock_client

    mock_repo.get_account_by_user_and_meta_id.return_value = None

    with pytest.raises(HTTPException) as exc:
        await connect_account(db_session, current_user=current_user, payload=payload)

    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "60"
    assert exc.value.detail["code"] == "RATE_LIMITED"
    mock_repo.create_account.assert_not_called()

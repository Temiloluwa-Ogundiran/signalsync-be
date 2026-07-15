import pytest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
from sqlalchemy import BigInteger

# Import domain models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.users.models  # noqa: F401
import app.domains.journal.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.domains.accounts.models import TradingAccount, TradingPlatform, TradingAccountType
from app.domains.accounts.models import Trade
from app.core.config import settings
from app.domains.accounts.sync import SyncResult, ingest_mt5_core_history_result, sync_account_deals_mt5

@pytest.fixture
def db_session() -> MagicMock:
    return MagicMock()

@pytest.fixture
def mock_account() -> TradingAccount:
    acct = TradingAccount()
    acct.id = uuid.uuid4()
    acct.broker_login = "123456"
    acct.broker_server = "BrokerServer"
    acct.broker_name = "Test Broker"
    acct.broker_utc_offset = 0
    acct.timezone = "UTC"
    return acct


def test_trade_magic_number_uses_bigint_for_mt5_values() -> None:
    assert isinstance(Trade.__table__.c.magic_number.type, BigInteger)

@patch("app.domains.accounts.sync.account_repo")
def test_ingest_mt5_core_history_result(mock_repo, db_session, mock_account) -> None:
    # Set up mock repository expectations
    mock_repo.bulk_upsert_closed_trades.return_value = (1, 0, [datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)])
    mock_repo.delete_trades_outside_valid_broker_ids_in_window.return_value = (1, {datetime(2026, 5, 20, tzinfo=timezone.utc).date()})

    payload = {
        "broker_offset_seconds": 7200,  # 2 hours
        "snapshot": {
            "captured_at": "2026-05-20T10:00:00Z",
            "balance": 10000.0,
            "equity": 10050.0,
            "floating_pnl": 50.0,
        },
        "deals": [
            {
                "ticket": "999888",
                "symbol": "EURUSD",
                "direction": "buy",
                "price_in": 1.0850,
                "price_out": 1.0870,
                "volume": 0.1,
                "gross_profit": 20.0,
                "commission": -1.5,
                "swap": -0.2,
                "time_setup": "2026-05-20T08:00:00Z",
                "time_closed": "2026-05-20T09:00:00Z",
                "position_id": "111222",
                "sl": 1.0800,
                "tp": 1.0900,
                "magic_number": 1780325659298,
                "trade_source": "personal",
                "mfe": 25.0,
                "mae": -5.0,
            }
        ]
    }

    # Execute
    result = ingest_mt5_core_history_result(
        db_session,
        account=mock_account,
        result=payload,
        closed_from_utc=datetime(2026, 5, 20, tzinfo=timezone.utc),
        closed_to_utc_exclusive=None,
        authoritative=True,
    )

    # Asserts
    assert result.inserted_trades == 1
    assert result.updated_trades == 0
    assert result.deleted_trades == 1
    assert result.changed_trades == 2
    assert result.touched_trading_dates == 1

    # Verify offset updated
    assert mock_account.broker_utc_offset == 7200
    db_session.flush.assert_called()
    # #7: ingest owns no transaction boundary — it flushes, the caller commits.
    # Committing here would also release the transaction-scoped sync advisory lock.
    db_session.commit.assert_not_called()

    # Verify snapshot persisted
    mock_repo.upsert_account_snapshot_for_date.assert_called_once_with(
        db_session,
        account_id=mock_account.id,
        snapshot_date=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc).date(),
        balance=Decimal("10000.0"),
        equity=Decimal("10050.0"),
        floating_pnl=Decimal("50.0"),
    )

    mock_repo.delete_trades_outside_valid_broker_ids_in_window.assert_called_once()

    # Verify trade upserted with correct mappings
    mock_repo.bulk_upsert_closed_trades.assert_called_once()
    _, kwargs = mock_repo.bulk_upsert_closed_trades.call_args
    rows = kwargs["rows"]
    assert len(rows) == 1
    row = rows[0]
    
    assert row["account_id"] == mock_account.id
    assert row["broker_trade_id"] == "999888"
    assert row["symbol"] == "EURUSD"
    assert row["open_price"] == Decimal("1.085")
    assert row["close_price"] == Decimal("1.087")
    assert row["volume"] == Decimal("0.1")
    assert row["profit"] == Decimal("20.0")
    assert row["commission"] == Decimal("-1.5")
    assert row["swap"] == Decimal("-0.2")
    assert row["net_profit"] == Decimal("18.3")
    assert row["duration_seconds"] == 3600
    assert row["position_id"] == "111222"
    assert row["sl"] == Decimal("1.08")
    assert row["tp"] == Decimal("1.09")
    assert row["magic_number"] == 1780325659298
    assert row["trade_source"].value == "personal"
    assert row["mfe"] == Decimal("25.0")
    assert row["mae"] == Decimal("-5.0")

@patch("app.domains.accounts.sync.account_repo")
def test_ingest_mt5_core_history_result_skips_authoritative_cleanup_for_empty_history(
    mock_repo,
    db_session,
    mock_account,
) -> None:
    mock_repo.bulk_upsert_closed_trades.return_value = (0, 0, [])

    result = ingest_mt5_core_history_result(
        db_session,
        account=mock_account,
        result={
            "broker_offset_seconds": 0,
            "deals": [],
        },
        closed_from_utc=datetime(2026, 5, 20, tzinfo=timezone.utc),
        closed_to_utc_exclusive=None,
        authoritative=True,
    )

    assert result.inserted_trades == 0
    assert result.touched_trading_dates == 0
    mock_repo.delete_trades_outside_valid_broker_ids_in_window.assert_not_called()
    mock_repo.bulk_upsert_closed_trades.assert_called_once()


@pytest.mark.anyio
@patch("app.domains.accounts.sync.ingest_mt5_core_history_result")
@patch("app.domains.accounts.mt5_core_client.Mt5CoreClient")
@patch("app.shared.utils.encryption.decrypt_secret")
@patch("app.domains.accounts.sync.account_repo")
async def test_manual_mt5_sync_default_window_matches_journal_range(
    mock_repo,
    mock_decrypt_secret,
    mock_client_cls,
    mock_ingest,
    db_session,
    mock_account,
) -> None:
    mock_account.encrypted_investor_password = "encrypted"
    mock_account.last_synced_at = None
    mock_decrypt_secret.return_value = "investor-password"
    mock_repo.try_acquire_account_sync_lock.return_value = True
    mock_repo.get_latest_account_snapshot_balance.return_value = Decimal("501103.19")
    mock_repo.count_closed_trades_for_accounts.return_value = {mock_account.id: 9}
    latest_closed_at = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
    mock_repo.get_latest_closed_trade_at.return_value = latest_closed_at
    mock_ingest.return_value = SyncResult(inserted_trades=0, touched_trading_dates=0)
    mock_client = AsyncMock()
    mock_client.submit_history_sync.return_value = {
        "deals": [],
        "history": {"raw_deals_count": 1, "raw_orders_count": 0},
        "broker_offset_seconds": 0,
    }
    mock_client_cls.return_value = mock_client

    await sync_account_deals_mt5(db_session, mock_account)

    _, sync_kwargs = mock_client.submit_history_sync.call_args
    assert datetime.now(timezone.utc) - sync_kwargs["from_time"] >= timedelta(days=29, hours=23)
    assert sync_kwargs["previous_balance"] == Decimal("501103.19")
    assert sync_kwargs["known_closed_trade_count"] == 9
    assert sync_kwargs["known_latest_closed_at"] == latest_closed_at
    mock_client_cls.assert_called_once_with(
        poll_interval=settings.MT5_CORE_SYNC_POLL_INTERVAL_SECONDS,
        worker_wait_timeout=settings.MT5_CORE_SYNC_WORKER_WAIT_TIMEOUT_SECONDS,
    )


@pytest.mark.anyio
@patch("app.domains.accounts.mt5_core_client.Mt5CoreClient")
@patch("app.shared.utils.encryption.decrypt_secret")
@patch("app.domains.accounts.sync.account_repo")
@patch("app.domains.accounts.sync.ingest_mt5_core_history_result")
async def test_initial_mt5_sync_empty_raw_history_completes_with_zero_trades(
    mock_ingest,
    mock_repo,
    mock_decrypt_secret,
    mock_client_cls,
    db_session,
    mock_account,
) -> None:
    mock_account.encrypted_investor_password = "encrypted"
    mock_account.last_synced_at = None
    mock_decrypt_secret.return_value = "investor-password"
    mock_repo.try_acquire_account_sync_lock.return_value = True
    mock_ingest.return_value = SyncResult(inserted_trades=0, touched_trading_dates=0)
    mock_client = AsyncMock()
    mock_client.submit_history_sync.return_value = {
        "deals": [],
        "history": {"raw_deals_count": 0, "raw_orders_count": 0},
        "broker_offset_seconds": 0,
    }
    mock_client_cls.return_value = mock_client

    result = await sync_account_deals_mt5(db_session, mock_account)

    assert result.inserted_trades == 0
    assert result.touched_trading_dates == 0
    mock_ingest.assert_called_once()

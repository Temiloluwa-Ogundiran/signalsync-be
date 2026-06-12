import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from decimal import Decimal

# Import domain models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.domains.accounts.models import TradingAccount, TradingPlatform, TradingAccountType
from app.domains.accounts.sync import ingest_mt5_core_history_result

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
                "magic_number": 12345,
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
    assert result.touched_trading_dates == 1

    # Verify offset updated
    assert mock_account.broker_utc_offset == 7200
    db_session.flush.assert_called()

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
    assert row["magic_number"] == 12345
    assert row["trade_source"].value == "personal"
    assert row["mfe"] == Decimal("25.0")
    assert row["mae"] == Decimal("-5.0")

    # Verify daily stats rebuilding
    mock_repo.rebuild_daily_stats_for_date.assert_called_once()

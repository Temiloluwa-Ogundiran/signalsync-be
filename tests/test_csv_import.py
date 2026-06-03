import io
from decimal import Decimal
import pytest

# Import domain models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.domains.csv_import.parsers.mt5_report import MT5ReportParser


def test_mt5_report_parser() -> None:
    # Read the real sample file from the workspace
    filepath = r"d:\Webbb\Syncgram\Synctrades\docs\ReportHistory-314495127.xlsx"
    with open(filepath, "rb") as f:
        file_bytes = f.read()

    parser = MT5ReportParser()
    result = parser.parse(io.BytesIO(file_bytes), "America/New_York")

    # Assert no errors
    assert len(result.errors) == 0, f"Expected 0 errors, got: {result.errors}"

    # Verify metadata
    assert result.account_meta.account_number == "314495127"
    assert result.account_meta.currency == "USD"
    assert result.account_meta.broker_server == "GoatFunded-Server"
    assert result.account_meta.account_type == "live"
    assert result.account_meta.broker_name == "Goat Funded Ltd."
    assert result.account_meta.starting_balance == Decimal("5000")
    assert result.account_meta.current_balance == Decimal("4643.86")

    # Verify trades
    assert len(result.trades) == 89
    assert result.parsed_trade_count == 89

    # Verify first trade details
    first_trade = result.trades[0]
    assert first_trade.broker_trade_id == "58456025"
    assert first_trade.symbol == "AUDUSD"  # suffix .x stripped
    assert first_trade.direction == "buy"
    assert first_trade.volume == Decimal("0.36")
    assert first_trade.open_price == Decimal("0.7057")
    assert first_trade.close_price == Decimal("0.70585")
    assert first_trade.profit == Decimal("5.4")
    assert first_trade.commission == Decimal("-1.8")
    assert first_trade.swap == Decimal("0")
    assert first_trade.sl == Decimal("0.70448")
    assert first_trade.tp == Decimal("0.70573")

import io
from decimal import Decimal
from datetime import datetime

import openpyxl

# Import domain models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401

from app.domains.csv_import.parsers.mt5_report import MT5ReportParser


def build_mt5_report_workbook_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active

    worksheet["A1"] = "Name:"
    worksheet["D1"] = "Primary Account"
    worksheet["A2"] = "Account:"
    worksheet["D2"] = "314495127 (USD, GoatFunded-Server, real, Hedge)"
    worksheet["A3"] = "Company:"
    worksheet["D3"] = "Goat Funded Ltd."

    worksheet["A6"] = "Positions"
    worksheet.append(
        [
            "Time",
            "Position",
            "Symbol",
            "Type",
            "Volume",
            "Price",
            "S / L",
            "T / P",
            "Time",
            "Price",
            "Commission",
            "Swap",
            "Profit",
        ]
    )
    worksheet.append(
        [
            datetime(2026, 5, 1, 9, 30, 0),
            "58456025",
            "AUDUSD.x",
            "buy",
            "0.36",
            "0.7057",
            "0.70448",
            "0.70573",
            datetime(2026, 5, 1, 10, 0, 0),
            "0.70585",
            "-1.8",
            "0",
            "5.4",
        ]
    )

    worksheet["A10"] = "Deals"
    worksheet.append(
        [
            "Time",
            "Deal",
            "Symbol",
            "Type",
            "Direction",
            "Volume",
            "Price",
            "Order",
            "Commission",
            "Fee",
            "Swap",
            "Profit",
            "Balance",
            "Comment",
        ]
    )
    worksheet.append(
        [
            datetime(2026, 5, 1, 0, 0, 0),
            "1",
            "",
            "balance",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "5000",
            "Initial balance",
        ]
    )
    worksheet.append(
        [
            datetime(2026, 5, 1, 10, 0, 0),
            "2",
            "AUDUSD.x",
            "deal",
            "buy",
            "0.36",
            "0.70585",
            "58456025",
            "-1.8",
            "0",
            "0",
            "5.4",
            "5003.6",
            "",
        ]
    )

    worksheet["A15"] = "Balance:"
    worksheet["D15"] = "4643.86"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_mt5_report_parser() -> None:
    parser = MT5ReportParser()
    result = parser.parse(
        io.BytesIO(build_mt5_report_workbook_bytes()),
        "America/New_York",
    )

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
    assert len(result.trades) == 1
    assert result.parsed_trade_count == 1

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
    assert "Symbol suffixes stripped" in result.warnings[0]

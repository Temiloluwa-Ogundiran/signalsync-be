import re
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import openpyxl

from app.domains.csv_import.parsers.base import (
    ParsedAccountMeta,
    ParsedTrade,
    ParseError,
    ParseResult,
    PlatformParser,
)

_UTC = ZoneInfo("UTC")

# Frozensets used as section-end sentinels inside tight loops — avoids
# tuple recreation on every iteration.
_POSITIONS_END = frozenset({"Orders", "Deals", "Working Orders", "Summary"})
_DEALS_END = frozenset({"Summary", "Balance:", "Working Orders"})


class _Sections(NamedTuple):
    positions_idx: int
    deals_idx: int
    balance_row_idx: int


class MT5ReportParser(PlatformParser):
    platform_id = "mt5"
    platform_display_name = "MetaTrader 5"
    supported_extensions = [".xlsx"]

    def parse(self, file_content: BytesIO, source_timezone: str) -> ParseResult:
        result = ParseResult()

        try:
            tz = ZoneInfo(source_timezone)
        except (ZoneInfoNotFoundError, KeyError):
            result.errors.append(ParseError(
                row_number=0,
                column="timezone",
                message=f"Invalid timezone name: {source_timezone}. Please choose a valid timezone.",
            ))
            return result

        rows = self._load_rows(file_content, result)
        if rows is None:
            return result

        result.raw_row_count = len(rows)

        account_meta, sections = self._scan_rows(rows)
        result.account_meta = account_meta

        if sections.positions_idx == -1:
            result.errors.append(ParseError(
                row_number=1,
                column=None,
                message=(
                    "Could not find 'Positions' section in the report history. "
                    "Please ensure this is a standard MT5 Trade History Report."
                ),
            ))
            return result

        trades, stripped_symbol_seen = self._parse_positions(rows, sections.positions_idx, tz, result)
        result.trades = trades
        result.parsed_trade_count = len(trades)

        if sections.deals_idx != -1:
            result.daily_balances = self._parse_deals(rows, sections.deals_idx, account_meta, tz)

        if account_meta.starting_balance is None:
            account_meta.starting_balance = account_meta.current_balance or Decimal("0")

        if stripped_symbol_seen:
            result.warnings.append("Symbol suffixes stripped (e.g., AUDUSD.x -> AUDUSD) for compatibility.")

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_rows(file_content: BytesIO, result: ParseResult) -> Optional[list]:
        """
        Open the XLSX in read-only mode, materialise all rows, then close.

        read_only streams cells lazily instead of building openpyxl's full
        in-memory object graph (which can be 10-50x the file size) — important
        for memory safety under concurrent uploads.  The workbook must be
        closed explicitly to release the underlying file handle.
        """
        wb = None
        try:
            wb = openpyxl.load_workbook(file_content, data_only=True, read_only=True)
            rows = list(wb.active.iter_rows(values_only=True))
        except (zipfile.BadZipFile, ValueError, TypeError, AttributeError, OSError) as e:
            result.errors.append(ParseError(
                row_number=0,
                column=None,
                message=f"Failed to open Excel file. Make sure it is a valid MT5 XLSX export. Error: {e}",
            ))
            return None
        finally:
            if wb is not None:
                wb.close()
        return rows

    @staticmethod
    def _scan_rows(rows: list) -> tuple[ParsedAccountMeta, _Sections]:
        """
        Single pass: extracts account metadata (first 15 rows) and locates
        section start indices (Positions, Deals, Balance summary).
        """
        meta = ParsedAccountMeta()
        positions_idx = deals_idx = balance_row_idx = -1

        for idx, row in enumerate(rows):
            if not row or row[0] is None:
                continue

            first = str(row[0]).strip()

            if idx < 15:
                val = row[3] if len(row) > 3 else None
                key = first.lower()
                if key == "account:" and val:
                    acct = str(val)
                    meta.account_number = MT5ReportParser._parse_account_number(acct)
                    meta.currency = MT5ReportParser._parse_currency(acct)
                    meta.broker_server = MT5ReportParser._parse_broker_server(acct)
                    meta.account_type = MT5ReportParser._parse_account_type(acct)
                elif key == "company:" and val:
                    meta.broker_name = str(val).strip()

            if first == "Positions":
                positions_idx = idx
            elif first == "Deals":
                deals_idx = idx
            elif first == "Balance:" and balance_row_idx == -1:
                balance_row_idx = idx

        if balance_row_idx != -1 and len(rows[balance_row_idx]) > 3:
            try:
                meta.current_balance = Decimal(str(rows[balance_row_idx][3]))
            except (InvalidOperation, ValueError, TypeError):
                pass

        return meta, _Sections(positions_idx, deals_idx, balance_row_idx)

    @staticmethod
    def _parse_positions(
        rows: list,
        positions_idx: int,
        tz: ZoneInfo,
        result: ParseResult,
    ) -> tuple[list[ParsedTrade], bool]:
        """
        Parse the Positions block into ParsedTrade objects.

        MT5 layout (0-indexed columns):
          0:Time(open)  1:Position  2:Symbol     3:Type     4:Volume
          5:Price(open) 6:S/L       7:T/P        8:Time(close)
          9:Price(close) 10:Commission 11:Swap   12:Profit
        """
        trades: list[ParsedTrade] = []
        stripped_symbol_seen = False

        for r_idx in range(positions_idx + 2, len(rows)):
            row = rows[r_idx]
            if not row or row[0] is None:
                break
            if str(row[0]).strip() in _POSITIONS_END:
                break

            row_num = r_idx + 1
            try:
                (
                    open_time_val, position_id_val, symbol_val, type_val, volume_val,
                    open_price_val, sl_val, tp_val, close_time_val, close_price_val,
                    commission_val, swap_val, profit_val,
                ) = row[:13]

                if not position_id_val or not symbol_val or not open_time_val or not close_time_val:
                    continue

                symbol = str(symbol_val).strip()
                if "." in symbol:
                    symbol = symbol.split(".")[0]
                    stripped_symbol_seen = True

                direction = "sell" if "sell" in str(type_val).strip().lower() else "buy"
                position_id = str(position_id_val).strip()

                trades.append(ParsedTrade(
                    broker_trade_id=position_id,
                    symbol=symbol,
                    direction=direction,
                    volume=Decimal(str(volume_val)),
                    open_price=Decimal(str(open_price_val)),
                    close_price=Decimal(str(close_price_val)),
                    opened_at=MT5ReportParser._parse_date(open_time_val, tz),
                    closed_at=MT5ReportParser._parse_date(close_time_val, tz),
                    profit=Decimal(str(profit_val or 0)),
                    commission=Decimal(str(commission_val or 0)),
                    swap=Decimal(str(swap_val or 0)),
                    sl=MT5ReportParser._parse_optional_decimal(sl_val),
                    tp=MT5ReportParser._parse_optional_decimal(tp_val),
                    position_id=position_id,
                ))

            except (ValueError, TypeError, IndexError, KeyError, InvalidOperation) as e:
                result.errors.append(ParseError(
                    row_number=row_num,
                    column=None,
                    message=f"Failed to parse row {row_num}: {e}",
                ))

        return trades, stripped_symbol_seen

    @staticmethod
    def _parse_deals(
        rows: list,
        deals_idx: int,
        meta: ParsedAccountMeta,
        tz: ZoneInfo,
    ) -> dict:
        """
        Parse the Deals block to extract:
        - starting_balance (first deal whose Type column == 'balance')
        - daily_balances (last recorded balance per local calendar date)

        MT5 Deals columns:
          0:Time  1:Deal  2:Symbol  3:Type  4:Direction  5:Volume  6:Price
          7:Order 8:Commission  9:Fee  10:Swap  11:Profit  12:Balance  13:Comment
        """
        daily_balances: dict = {}

        for r_idx in range(deals_idx + 2, len(rows)):
            row = rows[r_idx]
            if not row or row[0] is None:
                break
            if str(row[0]).strip() in _DEALS_END:
                break

            try:
                balance_val = (
                    Decimal(str(row[12])) if len(row) > 12 and row[12] is not None
                    else Decimal(str(row[11]))
                )

                if str(row[3]).strip().lower() == "balance" and meta.starting_balance is None:
                    meta.starting_balance = balance_val

                deal_date = MT5ReportParser._parse_date(row[0], tz).astimezone(tz).date()
                daily_balances[deal_date] = balance_val

            except (ValueError, TypeError, IndexError, KeyError, InvalidOperation):
                pass

        return daily_balances

    @staticmethod
    def _parse_optional_decimal(val) -> Optional[Decimal]:
        """Return Decimal for a non-zero numeric cell value, None otherwise."""
        if val is None:
            return None
        s = str(val).strip()
        if s in ("", "0", "0.0"):
            return None
        return Decimal(s)

    @staticmethod
    def _parse_date(val, tz: ZoneInfo) -> datetime:
        """Localise a broker naive datetime to `tz`, then convert to UTC."""
        naive_dt = val if isinstance(val, datetime) else datetime.strptime(str(val).strip(), "%Y.%m.%d %H:%M:%S")
        return naive_dt.replace(tzinfo=tz).astimezone(_UTC)

    # ------------------------------------------------------------------
    # Account field parsers — all operate on the single "Account:" cell,
    # format: "314495127 (USD, GoatFunded-Server, real, Hedge)"
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_account_number(val: str) -> Optional[str]:
        m = re.match(r"^(\d+)", val)
        return m.group(1) if m else None

    @staticmethod
    def _parse_currency(val: str) -> Optional[str]:
        m = re.search(r"\(([^,]+)", val)
        return m.group(1).strip() if m else "USD"

    @staticmethod
    def _parse_broker_server(val: str) -> Optional[str]:
        parts = val.split(",")
        return parts[1].strip() if len(parts) > 1 else None

    @staticmethod
    def _parse_account_type(val: str) -> str:
        parts = val.split(",")
        if len(parts) > 2:
            t = parts[2].strip().lower()
            return "live" if "real" in t or "live" in t else "demo"
        return "demo"

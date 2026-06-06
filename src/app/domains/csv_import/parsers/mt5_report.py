import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Optional
from zoneinfo import ZoneInfo
import openpyxl

from app.domains.csv_import.parsers.base import (
    ParsedAccountMeta,
    ParsedTrade,
    ParseError,
    ParseResult,
    PlatformParser,
)


class MT5ReportParser(PlatformParser):
    platform_id = "mt5"
    platform_display_name = "MetaTrader 5"
    supported_extensions = [".xlsx"]

    def parse(self, file_content: BytesIO, source_timezone: str) -> ParseResult:
        result = ParseResult()
        
        try:
            # Validate timezone
            ZoneInfo(source_timezone)
        except Exception:
            result.errors.append(
                ParseError(
                    row_number=0,
                    column="timezone",
                    message=f"Invalid timezone name: {source_timezone}. Please choose a valid timezone.",
                )
            )
            return result

        try:
            # Load workbook
            # openpyxl requires read_only=False to retrieve values or formulas if we don't evaluate them,
            # but read_only=True is faster. Let's use standard loading.
            wb = openpyxl.load_workbook(file_content, data_only=True)
            ws = wb.active
        except Exception as e:
            result.errors.append(
                ParseError(
                    row_number=0,
                    column=None,
                    message=f"Failed to open Excel file. Make sure it is a valid MT5 XLSX export. Error: {str(e)}",
                )
            )
            return result

        # Read the entire sheet into memory to scan quickly
        rows = list(ws.iter_rows(values_only=True))
        result.raw_row_count = len(rows)

        # 1. Parse metadata (rows 1-5 typical, but let's scan key-value pairs in the first 10 rows)
        account_meta = ParsedAccountMeta()
        for idx in range(min(15, len(rows))):
            row = rows[idx]
            if not row or not row[0]:
                continue
            
            key = str(row[0]).strip().lower()
            val = row[3] if len(row) > 3 else None
            
            if key == "name:" and val:
                # E.g. Name: Boluwamitife-...
                pass
            elif key == "account:" and val:
                # E.g. 314495127 (USD, GoatFunded-Server, real, Hedge)
                account_meta.account_number = self._parse_account_number(str(val))
                account_meta.currency = self._parse_currency(str(val))
                account_meta.broker_server = self._parse_broker_server(str(val))
                account_meta.account_type = self._parse_account_type(str(val))
            elif key == "company:" and val:
                account_meta.broker_name = str(val).strip()

        result.account_meta = account_meta

        # Find Positions, Deals, and Summary section start rows
        positions_idx = -1
        deals_idx = -1
        balance_row_idx = -1
        equity_row_idx = -1
        floating_pnl_row_idx = -1

        for idx, row in enumerate(rows):
            if not row or row[0] is None:
                continue
            first_val = str(row[0]).strip()
            if first_val == "Positions":
                positions_idx = idx
            elif first_val == "Deals":
                deals_idx = idx
            elif first_val == "Balance:":
                balance_row_idx = idx
            elif first_val == "Equity:":
                equity_row_idx = idx
            elif first_val == "Floating P/L:":
                floating_pnl_row_idx = idx

        # Extract Summary balances
        if balance_row_idx != -1 and len(rows[balance_row_idx]) > 3:
            try:
                account_meta.current_balance = Decimal(str(rows[balance_row_idx][3]))
            except Exception:
                pass

        # 2. Parse Positions (Trades)
        stripped_symbol_seen = False
        if positions_idx == -1:
            result.errors.append(
                ParseError(
                    row_number=1,
                    column=None,
                    message="Could not find 'Positions' section in the report history. Please ensure this is a standard MT5 Trade History Report.",
                )
            )
            return result

        # Next row is header, row after that is data
        start_pos_row = positions_idx + 2
        for r_idx in range(start_pos_row, len(rows)):
            row = rows[r_idx]
            if not row or row[0] is None:
                # Reached end of Positions section
                break
            
            first_val = str(row[0]).strip()
            if first_val in ("Orders", "Deals", "Working Orders", "Summary"):
                break
            
            row_num = r_idx + 1
            
            try:
                # Columns:
                # 0: Time (open)
                # 1: Position
                # 2: Symbol
                # 3: Type
                # 4: Volume
                # 5: Price (open)
                # 6: S / L
                # 7: T / P
                # 8: Time (close)
                # 9: Price (close)
                # 10: Commission
                # 11: Swap
                # 12: Profit
                
                open_time_val = row[0]
                position_id_val = row[1]
                symbol_val = row[2]
                type_val = row[3]
                volume_val = row[4]
                open_price_val = row[5]
                sl_val = row[6]
                tp_val = row[7]
                close_time_val = row[8]
                close_price_val = row[9]
                commission_val = row[10]
                swap_val = row[11]
                profit_val = row[12]

                if not position_id_val or not symbol_val or not open_time_val or not close_time_val:
                    continue

                # Strip symbol suffix
                symbol = str(symbol_val).strip()
                if "." in symbol:
                    symbol = symbol.split(".")[0]
                    stripped_symbol_seen = True

                direction = str(type_val).strip().lower()
                if "sell" in direction:
                    direction = "sell"
                else:
                    direction = "buy"

                opened_at = self._parse_date(open_time_val, source_timezone)
                closed_at = self._parse_date(close_time_val, source_timezone)
                
                volume = Decimal(str(volume_val))
                open_price = Decimal(str(open_price_val))
                close_price = Decimal(str(close_price_val))
                
                profit = Decimal(str(profit_val or 0))
                commission = Decimal(str(commission_val or 0))
                swap = Decimal(str(swap_val or 0))

                sl = Decimal(str(sl_val)) if sl_val is not None and str(sl_val).strip() not in ("", "0", "0.0") else None
                tp = Decimal(str(tp_val)) if tp_val is not None and str(tp_val).strip() not in ("", "0", "0.0") else None

                trade = ParsedTrade(
                    broker_trade_id=str(position_id_val).strip(),
                    symbol=symbol,
                    direction=direction,
                    volume=volume,
                    open_price=open_price,
                    close_price=close_price,
                    opened_at=opened_at,
                    closed_at=closed_at,
                    profit=profit,
                    commission=commission,
                    swap=swap,
                    sl=sl,
                    tp=tp,
                    position_id=str(position_id_val).strip(),
                )
                result.trades.append(trade)
                result.parsed_trade_count += 1

            except Exception as e:
                result.errors.append(
                    ParseError(
                        row_number=row_num,
                        column=None,
                        message=f"Failed to parse row {row_num}: {str(e)}",
                    )
                )

        # 3. Parse Deals to find the starting balance and daily balances
        if deals_idx != -1:
            start_deals_row = deals_idx + 2
            for r_idx in range(start_deals_row, len(rows)):
                row = rows[r_idx]
                if not row or row[0] is None:
                    break
                first_val = str(row[0]).strip()
                if first_val in ("Summary", "Balance:", "Working Orders"):
                    break
                
                # Headers: Time | Deal | Symbol | Type | Direction | Volume | Price | Order | Commission | Fee | Swap | Profit | Balance | Comment
                try:
                    deal_time_val = row[0]
                    # Index 12 is Balance, Index 11 is Profit (fallback)
                    if len(row) > 12 and row[12] is not None:
                        balance_val = Decimal(str(row[12]))
                    else:
                        balance_val = Decimal(str(row[11]))

                    # Extract starting balance (first deal of type 'balance')
                    if str(row[3]).strip().lower() == "balance" and account_meta.starting_balance is None:
                        account_meta.starting_balance = balance_val

                    # Record daily balances
                    deal_date = self._parse_date(deal_time_val, source_timezone).astimezone(ZoneInfo(source_timezone)).date()
                    result.daily_balances[deal_date] = balance_val
                except Exception:
                    pass

        # If starting balance was not found in deals, use default or current balance
        if account_meta.starting_balance is None:
            account_meta.starting_balance = account_meta.current_balance or Decimal("0")

        # Create warning if symbols were stripped
        if stripped_symbol_seen:
            result.warnings.append(
                "Symbol suffixes stripped (e.g., AUDUSD.x -> AUDUSD) for compatibility."
            )

        return result

    def _parse_account_number(self, val: str) -> Optional[str]:
        # E.g. "314495127 (USD, GoatFunded-Server, real, Hedge)" -> "314495127"
        match = re.match(r"^(\d+)", val)
        return match.group(1) if match else None

    def _parse_currency(self, val: str) -> Optional[str]:
        # E.g. "314495127 (USD, GoatFunded-Server, real, Hedge)" -> "USD"
        match = re.search(r"\(([^,]+)", val)
        return match.group(1).strip() if match else "USD"

    def _parse_broker_server(self, val: str) -> Optional[str]:
        # E.g. "314495127 (USD, GoatFunded-Server, real, Hedge)" -> "GoatFunded-Server"
        parts = val.split(",")
        return parts[1].strip() if len(parts) > 1 else None

    def _parse_account_type(self, val: str) -> str:
        # E.g. "314495127 (USD, GoatFunded-Server, real, Hedge)" -> "real"
        parts = val.split(",")
        if len(parts) > 2:
            t = parts[2].strip().lower()
            return "live" if "real" in t or "live" in t else "demo"
        return "demo"

    def _parse_date(self, val, timezone_str: str) -> datetime:
        if isinstance(val, datetime):
            naive_dt = val
        else:
            # Parse format: YYYY.MM.DD HH:MM:SS
            naive_dt = datetime.strptime(str(val).strip(), "%Y.%m.%d %H:%M:%S")

        # Localize naive datetime to source_timezone
        localized_dt = naive_dt.replace(tzinfo=ZoneInfo(timezone_str))
        # Convert to UTC
        return localized_dt.astimezone(ZoneInfo("UTC"))

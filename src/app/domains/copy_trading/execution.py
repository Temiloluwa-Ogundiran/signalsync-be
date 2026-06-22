import hashlib
import json
from decimal import Decimal

from app.domains.accounts.models import (
    ImportMethod,
    TradingAccountConnectionState,
    TradingPlatform,
)


def is_trade_ready(account) -> bool:
    return bool(
        account
        and account.platform == TradingPlatform.mt5
        and account.import_method == ImportMethod.auto_sync
        and account.connection_state == TradingAccountConnectionState.ready
        and not account.is_archived
        and account.encrypted_trader_password
    )


def calculate_signal_volume(
    *,
    fixed_lot: Decimal,
    take_profit_count: int,
    take_profit_mode: str,
    distribution: str,
) -> Decimal:
    selected_count = max(1, take_profit_count)
    if take_profit_mode in {"lowest", "highest"}:
        selected_count = 1
    if distribution == "split_total":
        return fixed_lot
    return fixed_lot * Decimal(selected_count)


def ensure_exposure_within_limit(
    *, current_exposure: Decimal, signal_volume: Decimal, maximum: Decimal
) -> None:
    if signal_volume > maximum or current_exposure + signal_volume > maximum:
        raise ValueError(
            f"Copied exposure would exceed the account maximum of {maximum}."
        )


def catalog_fingerprint(symbols: list[dict]) -> str:
    relevant = [
        {
            "name": str(item.get("name", "")),
            "trade_mode": int(item.get("trade_mode", 0) or 0),
            "contract_size": str(item.get("contract_size", 0)),
            "volume_min": str(item.get("volume_min", 0)),
            "volume_max": str(item.get("volume_max", 0)),
            "volume_step": str(item.get("volume_step", 0)),
        }
        for item in symbols
    ]
    payload = json.dumps(
        sorted(relevant, key=lambda item: item["name"]),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class BrokerSymbol:
    name: str
    contract_size: Decimal
    spread: int
    trade_mode: int
    visible: bool = True


def normalize_symbol(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    aliases = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "BTC": "BTCUSD"}
    return aliases.get(normalized, normalized)


def resolve_symbol(requested: str, symbols: list[BrokerSymbol]) -> BrokerSymbol:
    target = normalize_symbol(requested)
    tradable = [symbol for symbol in symbols if symbol.trade_mode not in {0, 3}]
    matches = [symbol for symbol in tradable if normalize_symbol(symbol.name).startswith(target)]
    if not matches:
        raise ValueError(f"No tradable broker symbol matches {requested}.")
    return min(
        matches,
        key=lambda symbol: (
            abs(len(normalize_symbol(symbol.name)) - len(target)),
            -symbol.contract_size,
            symbol.spread,
            not symbol.visible,
        ),
    )

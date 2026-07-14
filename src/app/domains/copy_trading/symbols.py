import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class BrokerSymbol:
    name: str
    contract_size: Decimal
    spread: int = 0
    trade_mode: int | str = 1
    visible: bool = True
    min_volume: Decimal = Decimal("0.01")
    max_volume: Decimal = Decimal("100")
    volume_step: Decimal = Decimal("0.01")
    filling_modes: tuple[str, ...] = ()
    execution_mode: str | None = None
    point: Decimal = Decimal("0.00001")


def broker_symbol_from_metaapi(specification: dict) -> BrokerSymbol:
    return BrokerSymbol(
        name=str(specification["symbol"]),
        contract_size=Decimal(str(specification.get("contractSize") or 0)),
        spread=int(specification.get("spread") or 0),
        trade_mode=specification.get("tradeMode", "SYMBOL_TRADE_MODE_DISABLED"),
        visible=bool(specification.get("visible", True)),
        min_volume=Decimal(str(specification.get("minVolume") or "0.01")),
        max_volume=Decimal(str(specification.get("maxVolume") or "100")),
        volume_step=Decimal(str(specification.get("volumeStep") or "0.01")),
        filling_modes=tuple(specification.get("fillingModes") or ()),
        execution_mode=specification.get("executionMode"),
        point=Decimal(str(specification.get("point") or "0.00001")),
    )


def normalize_volume(volume: Decimal, symbol: BrokerSymbol) -> Decimal:
    bounded = min(max(Decimal(volume), symbol.min_volume), symbol.max_volume)
    steps = (bounded / symbol.volume_step).to_integral_value(rounding="ROUND_FLOOR")
    normalized = steps * symbol.volume_step
    return max(symbol.min_volume, min(normalized, symbol.max_volume))


def normalize_symbol(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    aliases = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "BTC": "BTCUSD"}
    return aliases.get(normalized, normalized)


def resolve_symbol(requested: str, symbols: list[BrokerSymbol]) -> BrokerSymbol:
    target = normalize_symbol(requested)
    disabled_modes = {0, 3, "SYMBOL_TRADE_MODE_DISABLED", "SYMBOL_TRADE_MODE_CLOSEONLY"}
    tradable = [symbol for symbol in symbols if symbol.trade_mode not in disabled_modes]
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

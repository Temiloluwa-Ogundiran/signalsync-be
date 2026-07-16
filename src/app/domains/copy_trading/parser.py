import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

from app.domains.copy_trading.engine import SignalAction


class AiAction(BaseModel):
    action: SignalAction
    symbol: str | None = None
    direction: Literal["buy", "sell"] | None = None
    order_type: str | None = None
    entry: Decimal | None = None
    entry_high: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profits: list[Decimal] = Field(default_factory=list)
    close_fraction: Decimal | None = None
    explicit_reference: str | None = None
    confidence: float = Field(ge=0, le=1)


_NUMBER = r"[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
_LABEL_SEPARATOR = r"(?:[:=@]|\b(?:TO|AT|IS)\b)"
_RESERVED_TOKENS = {
    "ABOVE",
    "ADD",
    "ALL",
    "BE",
    "BREAKEVEN",
    "BUY",
    "CANCEL",
    "CLOSE",
    "DELETE",
    "ENTRY",
    "EXIT",
    "GOLD",
    "HALF",
    "HIT",
    "LIMIT",
    "LONG",
    "LOSS",
    "MARKET",
    "MOVE",
    "NOW",
    "ORDER",
    "PENDING",
    "PROFIT",
    "SELL",
    "SET",
    "SHORT",
    "SL",
    "STOP",
    "TAKE",
    "TP",
}
_SYMBOL_ALIASES = {
    "GOLD": "XAUUSD",
    "SILVER": "XAGUSD",
}
_CURRENCY_CODES = {"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"}


def _looks_like_symbol(value: str) -> bool:
    core = re.sub(r"[^A-Z0-9]", "", value)
    if len(core) >= 6 and (
        core[:3] in _CURRENCY_CODES | {"XAU", "XAG", "BTC", "ETH"}
        and core[3:6] in _CURRENCY_CODES | {"BTC", "ETH"}
    ):
        return True
    return bool(re.fullmatch(r"(?:US|NAS|GER|DE|UK|JP|HK)\d{2,4}[A-Z]?", core))


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _symbols(text: str) -> list[str]:
    upper = text.upper()
    found: list[str] = []

    def add(value: str) -> None:
        if value not in found:
            found.append(value)

    for base, quote in re.findall(r"\b([A-Z]{3})\s*/\s*([A-Z]{3})\b", upper):
        candidate = f"{base}{quote}"
        if _looks_like_symbol(candidate):
            add(candidate)
    for alias, canonical in _SYMBOL_ALIASES.items():
        if re.search(rf"\b{alias}\b", upper):
            add(canonical)
    candidates = re.findall(r"\b[A-Z][A-Z0-9._-]{2,15}\b", upper)
    for candidate in candidates:
        normalized = re.sub(r"[^A-Z0-9]", "", candidate)
        if normalized in _RESERVED_TOKENS or normalized.startswith(("TP", "SL")):
            continue
        if _looks_like_symbol(normalized):
            add(normalized)
    return found


def _symbol(text: str) -> str | None:
    symbols = _symbols(text)
    return symbols[0] if symbols else None


def _labelled_number(text: str, labels: str) -> Decimal | None:
    match = re.search(
        rf"(?:{labels})\s*(?:{_LABEL_SEPARATOR})?\s*({_NUMBER})\b",
        text,
        re.I,
    )
    return _decimal(match.group(1)) if match else None


def _take_profits(text: str) -> list[Decimal]:
    values = re.findall(
        rf"(?:\bTP(?:\d+)?\b|\bTAKE\s+PROFIT\b)"
        rf"\s*(?:{_LABEL_SEPARATOR})?\s*({_NUMBER})\b",
        text,
        re.I,
    )
    return [value for raw in values if (value := _decimal(raw)) is not None]


def _entry_range(text: str) -> tuple[Decimal | None, Decimal | None]:
    match = re.search(
        rf"\bENTRY\b\s*(?:[:=@]|IS|AT)?\s*({_NUMBER})"
        rf"\s*(?:-|\u2013|\u2014|TO)\s*({_NUMBER})",
        text,
        re.I,
    )
    if not match:
        return None, None
    return _decimal(match.group(1)), _decimal(match.group(2))


def _entry(text: str, *, pending: bool) -> Decimal | None:
    ranged, _ = _entry_range(text)
    if ranged is not None:
        return ranged
    labelled = _labelled_number(text, r"ENTRY")
    if labelled is not None:
        return labelled
    entry_text = re.sub(
        rf"(?:\bSL\b|\bSTOP\s+LOSS\b|\bTP(?:\d+)?\b|\bTAKE\s+PROFIT\b)"
        rf"\s*(?:{_LABEL_SEPARATOR})?\s*{_NUMBER}\b",
        " ",
        text,
        flags=re.I,
    )
    at_price = re.search(rf"(?:@|\bAT\b)\s*({_NUMBER})\b", entry_text, re.I)
    if at_price:
        return _decimal(at_price.group(1))
    if pending:
        pending_price = re.search(
            rf"\b(?:LIMIT|STOP)\b(?:\s+[A-Z][A-Z0-9._-]*)?\s*(?:@|AT)?\s*({_NUMBER})\b",
            text,
            re.I,
        )
        if pending_price:
            return _decimal(pending_price.group(1))
    return None


def _has_unlabelled_number(text: str, symbol: str | None) -> bool:
    cleaned = text
    cleaned = re.sub(
        rf"(?:\bSL\b|\bSTOP\s+LOSS\b|\bTP(?:\d+)?\b|\bTAKE\s+PROFIT\b)"
        rf"\s*(?:{_LABEL_SEPARATOR})?\s*{_NUMBER}\b",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        rf"\bENTRY\b\s*(?:[:=@]|IS|AT)?\s*{_NUMBER}"
        rf"(?:\s*(?:-|\u2013|\u2014|TO)\s*{_NUMBER})?\b",
        " ",
        cleaned,
        flags=re.I,
    )
    if symbol:
        cleaned = re.sub(rf"\b{re.escape(symbol)}\b", " ", cleaned, flags=re.I)
        for alias, canonical in _SYMBOL_ALIASES.items():
            if canonical == symbol:
                cleaned = re.sub(rf"\b{alias}\b", " ", cleaned, flags=re.I)
    return re.search(_NUMBER, cleaned) is not None


def deterministic_parse(text: str) -> AiAction | None:
    compact = " ".join((text or "").strip().split())
    if not compact:
        return None
    upper = compact.upper()
    if re.search(r"\b(?:IF|MAYBE|MIGHT|POSSIBLY|WATCH|LOOKING)\b", upper):
        return None
    symbol = _symbol(compact)

    if re.search(
        r"\b(?:TP|TAKE\s+PROFIT)\s+(?:HIT|REACHED)\b"
        r"|\bSTOPPED\s+OUT\b|\bSL\s+HIT\b",
        upper,
    ):
        return AiAction(action=SignalAction.status_only, symbol=symbol, confidence=1)

    non_actionable_context = re.search(
        r"\b(?:DO\s+NOT|DON['\u2019]?T|IGNORE|AVOID)\s+(?:BUY|SELL|LONG|SHORT)\b"
        r"|\bCANCEL\s+(?:THE\s+)?(?:BUY|SELL|LONG|SHORT)\b"
        r"|\b(?:YESTERDAY|RESULTS?|EXAMPLE|BACKTEST)\b"
        r"|\b(?:WE\s+)?(?:BOUGHT|SOLD)\b",
        upper,
    )
    has_trade_details = re.search(
        r"\b(?:BUY|SELL|LONG|SHORT|BOUGHT|SOLD|SL|TP\d*|ENTRY)\b",
        upper,
    )
    if non_actionable_context and has_trade_details:
        return AiAction(action=SignalAction.status_only, symbol=symbol, confidence=0)

    if re.search(r"\b(?:MOVE\s+SL\s+TO\s+)?(?:BE|BREAK[ -]?EVEN)\b", upper):
        return AiAction(action=SignalAction.break_even, symbol=symbol, confidence=1)

    if re.search(
        r"\b(?:CANCEL\s+(?:THE\s+)?PENDING|DELETE\s+.*\b(?:LIMIT|STOP)\b)",
        upper,
    ):
        return AiAction(action=SignalAction.cancel_pending, symbol=symbol, confidence=1)

    percent = re.search(rf"\bCLOSE\s+({_NUMBER})\s*%", upper)
    if percent:
        return AiAction(
            action=SignalAction.partial_close,
            symbol=symbol,
            close_fraction=_decimal(percent.group(1)) / Decimal("100"),
            confidence=1,
        )
    if re.search(r"\bCLOSE\s+(?:HALF|1/2)\b", upper):
        return AiAction(
            action=SignalAction.partial_close,
            symbol=symbol,
            close_fraction=Decimal("0.5"),
            confidence=1,
        )
    if re.search(r"\b(?:CLOSE|EXIT)\s+(?:ALL\s+)?[A-Z0-9._-]+\b", upper):
        return AiAction(action=SignalAction.full_close, symbol=symbol, confidence=1)

    take_profits = _take_profits(compact)
    stop_loss = _labelled_number(compact, r"SL|STOP\s+LOSS")
    additional_tp = re.search(
        r"\bTP([2-9]\d*)\b|\bADD\s+(?:A\s+)?TAKE\s+PROFIT\b",
        upper,
    )
    has_direction = re.search(r"\b(BUY|SELL|LONG|SHORT)\b", upper)
    if additional_tp and not has_direction and take_profits:
        return AiAction(
            action=SignalAction.additional_tp,
            symbol=symbol,
            take_profits=take_profits,
            confidence=1,
        )

    if not has_direction and (stop_loss is not None or take_profits):
        return AiAction(
            action=SignalAction.modify_sl_tp,
            symbol=symbol,
            stop_loss=stop_loss,
            take_profits=take_profits,
            confidence=1,
        )

    if not has_direction or symbol is None:
        return None

    direction_words = re.findall(r"\b(BUY|SELL|LONG|SHORT)\b", upper)
    direction_sides = {
        "buy" if value in {"BUY", "LONG"} else "sell"
        for value in direction_words
    }
    if len(direction_sides) > 1 or len(_symbols(compact)) > 1:
        return AiAction(action=SignalAction.status_only, confidence=0)

    direction = {
        "BUY": "buy",
        "LONG": "buy",
        "SELL": "sell",
        "SHORT": "sell",
    }[has_direction.group(1)]
    pending_keyword = re.search(r"\b(LIMIT|STOP)\b", upper)
    pending = pending_keyword is not None
    entry, entry_high = _entry_range(compact)
    if entry is None:
        entry = _entry(compact, pending=pending)
    if not pending and entry is None and _has_unlabelled_number(compact, symbol):
        return None
    return AiAction(
        action=SignalAction.place_pending if pending else SignalAction.open_market,
        symbol=symbol,
        direction=direction,
        order_type=pending_keyword.group(1).lower() if pending_keyword else None,
        entry=entry,
        entry_high=entry_high,
        stop_loss=stop_loss,
        take_profits=take_profits,
        confidence=1,
    )

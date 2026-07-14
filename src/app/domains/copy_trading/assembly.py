from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid


def normalize_symbol(value: str | None) -> str | None:
    if not value:
        return None
    return "".join(character for character in value.upper() if character.isalnum())


def normalize_direction(value: str | None) -> str | None:
    return value.strip().lower() if value else None


@dataclass(frozen=True)
class ConversationCandidate:
    id: uuid.UUID
    reply_root_message_id: int | None
    last_message_id: int
    symbol: str | None
    direction: str | None
    updated_at: datetime
    opening_submitted: bool = False
    has_active_trade: bool = False


@dataclass(frozen=True)
class ConversationChoice:
    selected: ConversationCandidate | None
    ambiguous: bool = False


def choose_conversation(
    *,
    reply_to_message_id: int | None,
    symbol: str | None,
    direction: str | None,
    candidates: list[ConversationCandidate],
    action: str | None = None,
    message_id: int | None = None,
    is_edit: bool = False,
) -> ConversationChoice:
    ordered = sorted(candidates, key=lambda item: item.updated_at, reverse=True)
    management_actions = {
        "modify_sl_tp",
        "break_even",
        "partial_close",
        "full_close",
        "cancel_pending",
        "additional_tp",
    }
    if action == "modify_sl_tp":
        eligible = [
            item
            for item in ordered
            if not item.opening_submitted or item.has_active_trade
        ]
    elif action in management_actions:
        eligible = [item for item in ordered if item.has_active_trade]
    else:
        eligible = ordered

    # A standalone SL/TP line immediately following an incomplete opening is
    # part of that signal, not an instruction for an older live position.
    if action == "modify_sl_tp":
        normalized_symbol = normalize_symbol(symbol)
        normalized_direction = normalize_direction(direction)
        incomplete_openings = [
            item
            for item in eligible
            if not item.opening_submitted
            and (
                normalized_symbol is None
                or normalize_symbol(item.symbol) == normalized_symbol
            )
            and (
                normalized_direction is None
                or normalize_direction(item.direction) == normalized_direction
            )
        ]
        if len(incomplete_openings) == 1:
            return ConversationChoice(incomplete_openings[0])
        if len(incomplete_openings) > 1:
            return ConversationChoice(None, ambiguous=True)

    if is_edit and message_id is not None:
        edit_matches = [
            item
            for item in eligible
            if message_id in {item.reply_root_message_id, item.last_message_id}
        ]
        if len(edit_matches) == 1:
            return ConversationChoice(edit_matches[0])
        if len(edit_matches) > 1:
            return ConversationChoice(None, ambiguous=True)
    if reply_to_message_id is not None:
        reply_matches = [
            item
            for item in eligible
            if reply_to_message_id
            in {item.reply_root_message_id, item.last_message_id}
        ]
        if len(reply_matches) == 1:
            return ConversationChoice(reply_matches[0])
        if len(reply_matches) > 1:
            return ConversationChoice(None, ambiguous=True)

    matchable = (
        [item for item in eligible if not item.opening_submitted]
        if action in {"open_market", "place_pending"}
        else eligible
    )
    normalized_symbol = normalize_symbol(symbol)
    normalized_direction = normalize_direction(direction)
    if normalized_symbol:
        exact = [
            item
            for item in matchable
            if normalize_symbol(item.symbol) == normalized_symbol
            and (
                normalized_direction is None
                or normalize_direction(item.direction) == normalized_direction
            )
        ]
        if len(exact) == 1:
            return ConversationChoice(exact[0])
        if len(exact) > 1:
            return ConversationChoice(None, ambiguous=True)
        unresolved = [
            item
            for item in matchable
            if normalize_symbol(item.symbol) is None
            and (
                normalized_direction is None
                or normalize_direction(item.direction) in {None, normalized_direction}
            )
        ]
        if len(unresolved) == 1:
            return ConversationChoice(unresolved[0])
        if len(unresolved) > 1:
            return ConversationChoice(None, ambiguous=True)
        return ConversationChoice(None)

    # Action-only updates may safely use an implicit conversation only when
    # there is exactly one possible target.
    if len(matchable) == 1:
        candidate = matchable[0]
        if normalized_direction and normalize_direction(candidate.direction) != normalized_direction:
            return ConversationChoice(None)
        return ConversationChoice(candidate)
    return ConversationChoice(None, ambiguous=len(matchable) > 1)


def route_deadline(now: datetime, assembly_window_seconds: int | None) -> datetime:
    return now + timedelta(seconds=assembly_window_seconds or 90)


def merge_context(current: dict, update: dict) -> dict:
    merged = dict(current)
    for key, value in update.items():
        if value not in (None, [], ""):
            merged[key] = value
    return merged

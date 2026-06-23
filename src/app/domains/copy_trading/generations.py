from app.domains.copy_trading.assembly import merge_context
from app.domains.copy_trading.models import RouteAssemblyState


OPEN_ACTIONS = {"open_market", "place_pending"}
MANAGEMENT_ACTIONS = {
    "modify_sl_tp",
    "break_even",
    "partial_close",
    "full_close",
    "cancel_pending",
    "additional_tp",
}


def merge_generation_context(
    current: dict,
    update: dict,
    *,
    opening_submitted: bool,
) -> dict:
    merged = merge_context(current, update)
    current_action = current.get("action")
    update_action = update.get("action")
    if (
        not opening_submitted
        and current_action in OPEN_ACTIONS
        and update_action in MANAGEMENT_ACTIONS
    ):
        merged["action"] = current_action
    return merged


def mark_generation_submitted(assembly, intent_id, now) -> None:
    assembly.state = RouteAssemblyState.executing
    if assembly.opening_intent_id is None:
        assembly.opening_intent_id = intent_id
    assembly.accepted_at = now
    assembly.completed_at = None
    assembly.terminal_reason = None


def mark_generation_completed(assembly, now) -> None:
    assembly.state = RouteAssemblyState.completed
    assembly.completed_at = now
    assembly.terminal_reason = None


def mark_generation_failed(assembly, reason: str, now) -> None:
    assembly.state = RouteAssemblyState.failed
    assembly.completed_at = now
    assembly.terminal_reason = reason


def mark_generation_expired(assembly, reason: str, now) -> None:
    assembly.state = RouteAssemblyState.expired
    assembly.completed_at = now
    assembly.terminal_reason = reason

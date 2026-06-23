from app.domains.copy_trading.assembly import merge_context


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

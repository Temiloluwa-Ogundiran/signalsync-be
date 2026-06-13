from typing import List

from langchain_core.runnables import RunnableConfig


def enforce_account_scope(account_ids: List[str], config: RunnableConfig) -> List[str]:
    """Clamp model-supplied account IDs to the server-generated turn scope."""
    allowed = config.get("configurable", {}).get("account_ids", [])
    allowed_ids = [str(account_id) for account_id in allowed]
    allowed_set = set(allowed_ids)
    if not allowed_set:
        return []

    scoped = [str(account_id) for account_id in account_ids if str(account_id) in allowed_set]
    return scoped or allowed_ids

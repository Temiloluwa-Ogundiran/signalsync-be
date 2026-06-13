import re
from typing import Optional


SAFE_INTERNALS_REFUSAL = (
    "I can't share internal database queries, table names, account IDs, or implementation details. "
    "Tell me what trading data you want to see, and I'll fetch or summarize it for you."
)

_INTERNAL_DISCLOSURE_PATTERNS = (
    re.compile(r"\bsql\b", re.I),
    re.compile(r"\b(select|from|where|join|order\s+by|group\s+by)\b.*\b(trades?|accounts?|table|database)\b", re.I),
    re.compile(r"\b(database|schema|table|column|query)\b.*\b(trades?|accounts?|account_id|uuid)\b", re.I),
    re.compile(r"\b(account_id|uuid|raw\s+id|internal\s+id)\b", re.I),
    re.compile(r"\b(show|give|write|generate|fetch|provide)\b.*\b(query|schema|table|sql)\b", re.I),
)


def internal_disclosure_response(content: str) -> Optional[str]:
    """Return a safe response for requests to expose implementation internals."""
    normalized = " ".join(content.strip().split())
    if not normalized:
        return None

    for pattern in _INTERNAL_DISCLOSURE_PATTERNS:
        if pattern.search(normalized):
            return SAFE_INTERNALS_REFUSAL
    return None

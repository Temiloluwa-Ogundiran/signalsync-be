"""
query_trades: catch-all tool that generates a read-only SQL SELECT from
the trader's natural-language question and executes it safely.

Account scope is enforced in code (CTE), never delegated to the LLM.
The LLM sees only `scoped_trades`; the outer CTE adds the WHERE clause.
"""
import logging
import re
from typing import Annotated, List, Optional, Tuple

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache
from langchain_openai import ChatOpenAI
from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger("synctrades.ai.tools.trade_query")

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 10_000

_TABLE_INFO = """
Relation available to you: scoped_trades
  This is the `trades` table, already filtered to the current trader's accounts.
  Query ONLY scoped_trades. Never reference `trades` directly. Do NOT write a
  top-level WITH/CTE — use subqueries if you need intermediate aggregation.

Columns (scoped_trades):
  id, account_id, symbol, direction ('buy'/'sell'), open_price, close_price,
  volume, profit (gross, never use for P&L), commission, swap, fee,
  net_profit (REAL P&L — always use this), stop_loss, take_profit, pips,
  percent_gain, result ('win'/'loss'/'breakeven'), duration_seconds, session,
  opened_at, closed_at (timestamptz)

Business rules:
  WIN = net_profit > 0  |  LOSS = net_profit < 0  |  BREAKEVEN = net_profit = 0
  Profit factor = SUM(net_profit) FILTER (WHERE net_profit > 0)
                  / NULLIF(ABS(SUM(net_profit) FILTER (WHERE net_profit < 0)), 0)
  Alias cost sums so the column name matches the cost type
  (e.g. SUM(commission) AS total_commission, SUM(fee) AS total_fees).
"""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", f"""You are a PostgreSQL expert. Translate the trader's question into ONE PostgreSQL SELECT query.

Hard rules:
- Output ONLY the raw SQL — no prose, no markdown, no code fences.
- Read only from scoped_trades. Never reference `trades`.
- A single SELECT. No semicolons, no second statement, no top-level WITH/CTE.
- Use net_profit for P&L, never profit. Alias cost sums so the column names the cost.

Schema:
{_TABLE_INFO}
"""),
    ("human", "{question}"),
])

_llm: ChatOpenAI | None = None
_chain = None

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|merge|"
    r"into|call|do|vacuum|reindex|cluster|lock|set|begin|commit|rollback)\b",
    re.I,
)
_BARE_TRADES = re.compile(r"(?<![\w.])trades\b", re.I)


def _get_chain():
    global _llm, _chain
    if _chain is None:
        _llm = ChatOpenAI(
            model=settings.AI_MODEL,
            temperature=0.0,
            openai_api_key=settings.OPENAI_API_KEY,
        )
        _chain = _PROMPT | _llm
    return _chain


def _strip_fences(sql: str) -> str:
    sql = sql.strip()
    if sql.startswith("```"):
        sql = "\n".join(sql.split("\n")[1:])
    if sql.endswith("```"):
        sql = "\n".join(sql.split("\n")[:-1])
    return sql.strip()


def _validate(sql: str) -> Optional[str]:
    s = sql.rstrip().rstrip(";").strip()
    if not s:
        return "empty query"
    if ";" in s:
        return "only one statement is allowed"
    if not re.match(r"(?is)^select\b", s):
        return "must be a single SELECT (no top-level WITH/CTE)"
    if _FORBIDDEN.search(s):
        return "only read-only SELECTs are allowed"
    if _BARE_TRADES.search(s):
        return "read from scoped_trades, not trades"
    return None


def _scope(account_ids: List[str]) -> Tuple[str, dict]:
    placeholders = ", ".join(f":a{i}" for i in range(len(account_ids)))
    cte = (
        "WITH scoped_trades AS (\n"
        f"    SELECT * FROM trades WHERE account_id = ANY(ARRAY[{placeholders}]::uuid[])\n"
        ")\n"
    )
    params = {f"a{i}": aid for i, aid in enumerate(account_ids)}
    return cte, params


def _execute(model_sql: str, account_ids: List[str]) -> str:
    cte, params = _scope(account_ids)
    final_sql = cte + model_sql
    logger.debug("[query_trades SQL]\n%s", final_sql)

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
        rows = db.execute(text(final_sql), params).fetchall()
        db.rollback()

    if not rows:
        return "No results found."

    cols = rows[0]._fields if hasattr(rows[0], "_fields") else [f"col{i}" for i in range(len(rows[0]))]
    lines = [" | ".join(str(c) for c in cols), "-" * 40]
    for row in rows[:MAX_ROWS]:
        lines.append(" | ".join(str(v) for v in row))
    if len(rows) > MAX_ROWS:
        lines.append(f"... ({len(rows) - MAX_ROWS} more rows truncated — refine the question)")
    return "\n".join(lines)


@tool
@tool_cache()
def query_trades(
    question: Annotated[str, "The trader's question, in plain language"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
) -> str:
    """Catch-all for any data question the dedicated tools don't cover. Writes a
    scoped, read-only SQL query against the trader's trades and can answer almost anything.

    ALWAYS use this rather than guessing or declining when no other tool cleanly fits.
    No data question should go unanswered because no named tool matched it.

    Typical uses: total commission / swap / broker fees, unusual multi-condition
    filters, one-off aggregates, anything bespoke."""
    chain = _get_chain()
    feedback: Optional[str] = None
    last_problem: Optional[str] = None

    for _ in range(2):
        q = question if feedback is None else (
            f"{question}\n\n[Your previous SQL was rejected: {feedback}. "
            f"Return a corrected single SELECT over scoped_trades.]"
        )
        raw = chain.invoke({"question": q}).content.strip()
        sql = _strip_fences(raw)

        problem = _validate(sql)
        if problem:
            feedback, last_problem = problem, problem
            continue

        try:
            return _execute(sql, account_ids)
        except Exception as err:
            feedback = f"execution error: {err}"
            last_problem = str(err)

    return (
        f"I couldn't build a safe query for that one ({last_problem}). "
        f"Try rephrasing or use a dedicated tool."
    )

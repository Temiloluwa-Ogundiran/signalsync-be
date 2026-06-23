"""
Trade-level AI review.

Generates a focused coach's review of ONE trade — the entry/exit, sizing,
hold, result, and (when present) the trader's own note and self-ratings — plus
an optional single behavioural insight.

Cached in ai_insights (kind='trade_review', keyed by trade id) so re-opening a
trade is instant and free. Pass refresh=True to regenerate.

Best-effort: any LLM failure returns a graceful fallback review built from the
trade's numbers, so the panel always shows something useful.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from app.core.config import settings
from app.domains.ai import repository as repo

logger = logging.getLogger("synctrades.ai.trade_review")

_SYSTEM = (
    "You are Partna AI, a trading coach reviewing ONE individual trade for a "
    "trader. You are given the trade's details as JSON (symbol, direction, "
    "entry/exit, sizing, hold time, result, the favorable/adverse excursion, "
    "and — when present — the trader's own note and self-ratings). Write a "
    "tight, specific review in 2-3 sentences: judge the entry/exit and sizing, "
    "note what was done well, and where execution or discipline could improve. "
    "Be concrete — reference the actual numbers. No filler, no greetings, no "
    "markdown headers. Plain, direct second person ('you'). Then, ONLY if there "
    "is a clear behavioural risk worth flagging (chasing, oversizing, cutting "
    "winners early, holding losers), add a one-sentence warning.\n\n"
    "Reply as STRICT JSON: {\"review\": \"...\", \"insight\": \"...\"}. Set "
    "\"insight\" to an empty string when there's nothing worth flagging. Nothing "
    "outside the JSON object."
)

_llm: Optional[ChatOpenAI] = None


def _get_llm() -> Optional[ChatOpenAI]:
    global _llm
    if not settings.OPENAI_API_KEY:
        return None
    if _llm is None:
        kwargs = dict(
            model=settings.AI_MODEL,
            openai_api_key=settings.OPENAI_API_KEY,
            max_tokens=350,
        )
        # gpt-5.x reasoning models reject temperature (see agent.py).
        if not settings.AI_MODEL.startswith("gpt-5"):
            kwargs["temperature"] = 0.2
        _llm = ChatOpenAI(**kwargs)
    return _llm


def _fetch_trade(db, trade_id: uuid.UUID) -> Optional[dict]:
    """Pull one trade + its account_id and any journal note/ratings.

    Returns None when the trade does not exist. The caller is responsible for
    verifying the trade's account belongs to the requesting user.
    """
    row = db.execute(
        text(
            """
            SELECT t.account_id, t.symbol, t.direction, t.volume,
                   t.open_price, t.close_price, t.net_profit, t.commission,
                   t.swap, t.duration_seconds, t.session, t.setup, t.sl, t.tp,
                   t.mfe, t.mae, t.opened_at, t.closed_at,
                   tj.note_html, tj.rating, tj.execution_quality,
                   tj.setup_quality, tj.discipline_score
            FROM trades t
            LEFT JOIN trade_journals tj ON tj.trade_id = t.id
            WHERE t.id = :tid
            """
        ),
        {"tid": str(trade_id)},
    ).first()
    if row is None:
        return None

    def _f(v):
        return float(v) if v is not None else None

    return {
        "account_id": str(row.account_id),
        "context": {
            "symbol": row.symbol,
            "direction": row.direction,
            "volume": _f(row.volume),
            "open_price": _f(row.open_price),
            "close_price": _f(row.close_price),
            "net_profit": _f(row.net_profit),
            "commission": _f(row.commission),
            "swap": _f(row.swap),
            "hold_min": round((row.duration_seconds or 0) / 60, 1),
            "session": row.session,
            "setup": row.setup or None,
            "sl": _f(row.sl),
            "tp": _f(row.tp),
            "mfe": _f(row.mfe),
            "mae": _f(row.mae),
            "note": (row.note_html or None),
            "self_rating": row.rating,
            "execution_quality": row.execution_quality,
            "setup_quality": row.setup_quality,
            "discipline_score": row.discipline_score,
        },
    }


def _fallback_review(ctx: dict) -> Tuple[str, str]:
    """Deterministic review from the numbers when the LLM is unavailable."""
    net = ctx.get("net_profit") or 0.0
    sign = "+" if net >= 0 else "-"
    money = f"{sign}${abs(net):,.2f}"
    direction = (ctx.get("direction") or "").upper()
    return (
        f"You {direction.lower() or 'traded'} {ctx.get('symbol', 'this instrument')} "
        f"for a net of {money} over {ctx.get('hold_min', 0)} minutes.",
        "",
    )


def generate_trade_review(
    *,
    user_id: uuid.UUID,
    trade_id: uuid.UUID,
    owned_account_ids: set[str],
    refresh: bool = False,
) -> Optional[dict]:
    """Return {review, insight, trade_id, cached} or None if not owned/found.

    `owned_account_ids` is the set of account ids belonging to the user; the
    trade's account must be in it.
    """
    from app.core.database import SessionLocal

    # 1. Serve cache unless refresh requested.
    if not refresh:
        with SessionLocal() as db:
            cached = repo.get_trade_review(db, user_id=user_id, trade_id=trade_id)
        if cached:
            return {**cached, "trade_id": str(trade_id), "cached": True}

    with SessionLocal() as db:
        trade = _fetch_trade(db, trade_id)
    if trade is None or trade["account_id"] not in owned_account_ids:
        return None

    account_id = uuid.UUID(trade["account_id"])
    ctx = trade["context"]

    # 2. Generate via LLM (best-effort), else fall back to a numeric review.
    review, insight = _fallback_review(ctx)
    llm = _get_llm()
    if llm is not None:
        try:
            resp = llm.invoke(
                [
                    SystemMessage(content=_SYSTEM),
                    HumanMessage(content=json.dumps(ctx)[:6000]),
                ]
            )
            parsed = json.loads((resp.content or "").strip())
            review = (parsed.get("review") or review).strip()
            insight = (parsed.get("insight") or "").strip()
        except Exception:
            logger.exception("trade review generation failed for %s", trade_id)

    # 3. Cache and return.
    try:
        with SessionLocal() as db:
            repo.upsert_trade_review(
                db,
                user_id=user_id,
                account_id=account_id,
                trade_id=trade_id,
                payload={"review": review, "insight": insight},
            )
            db.commit()
    except Exception:
        logger.exception("failed to cache trade review")

    return {"review": review, "insight": insight, "trade_id": str(trade_id), "cached": False}

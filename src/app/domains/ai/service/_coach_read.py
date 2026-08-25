"""
Day-level "Coach's Read".

Generates a short, specific narrative for one trading day — what happened, what
worked, where discipline slipped — plus an optional single warning insight.

Cached in ai_insights (kind='daily_read', keyed by account_id + trading_date) so
re-opening a day is instant and free. Pass refresh=True to regenerate.

Best-effort: any LLM failure returns a graceful fallback read built from the
day's numbers, so the card always shows something useful.
"""
import json
import logging
import uuid
from datetime import date as date_cls, datetime, timezone
from typing import Optional, Tuple

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.ai import repository as repo

logger = logging.getLogger("synctrades.ai.coach_read")

_COACH_KIND = "daily_read"

_SYSTEM = (
    "You are SignalSync AI, a trading coach reviewing ONE trading day for a trader. "
    "You are given that day's trades and stats as JSON. Write a tight, specific "
    "'coach's read' of the day in 2-3 sentences: what happened, what worked, and "
    "where discipline slipped. Be concrete — name symbols, directions, and the "
    "day's net result. No filler, no greetings, no markdown headers. Plain, direct "
    "second person ('you'). Then, ONLY if there is a clear behavioural risk worth "
    "flagging (revenge entries, oversizing after a loss, giving back gains, "
    "overtrading), add a one-sentence warning.\n\n"
    "Reply as STRICT JSON: {\"read\": \"...\", \"insight\": \"...\"}. Set "
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


def _fetch_day(account_id: uuid.UUID, trading_date: date_cls) -> dict:
    """Pull the day's trades + simple aggregates for the prompt (UTC day match)."""
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT symbol, direction, volume, net_profit, session,
                       duration_seconds, setup, closed_at
                FROM trades
                WHERE account_id = :aid AND DATE(closed_at) = :d
                ORDER BY closed_at ASC
                """
            ),
            {"aid": str(account_id), "d": trading_date.isoformat()},
        ).fetchall()

    trades = [
        {
            "symbol": r.symbol,
            "direction": r.direction,
            "volume": float(r.volume),
            "net_profit": float(r.net_profit),
            "session": r.session,
            "hold_min": round((r.duration_seconds or 0) / 60, 1),
            "setup": r.setup or None,
            "time": str(r.closed_at)[11:16],
        }
        for r in rows
    ]
    net = round(sum(t["net_profit"] for t in trades), 2)
    wins = sum(1 for t in trades if t["net_profit"] > 0)
    return {
        "trading_date": trading_date.isoformat(),
        "trade_count": len(trades),
        "net_pnl": net,
        "wins": wins,
        "losses": len(trades) - wins,
        "trades": trades,
    }


def _fallback_read(day: dict) -> Tuple[str, str]:
    """Deterministic read from the numbers when the LLM is unavailable."""
    n = day["trade_count"]
    if n == 0:
        return ("No trades closed on this day.", "")
    net = day["net_pnl"]
    sign = "+" if net >= 0 else "-"
    money = f"{sign}${abs(net):,.2f}"
    return (
        f"You closed {n} trade{'s' if n != 1 else ''} for a net of {money} "
        f"({day['wins']}W / {day['losses']}L).",
        "",
    )


def generate_coach_read(
    *,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    trading_date: date_cls,
    refresh: bool = False,
) -> dict:
    """Return {read, insight, trading_date, cached}. Generates + caches on miss."""
    # 1. Serve cache unless refresh requested.
    if not refresh:
        with SessionLocal() as db:
            cached = repo.get_daily_read(
                db, user_id=user_id, account_id=account_id, trading_date=trading_date
            )
        if cached:
            return {**cached, "trading_date": trading_date.isoformat(), "cached": True}

    day = _fetch_day(account_id, trading_date)

    if day["trade_count"] == 0:
        read, insight = _fallback_read(day)
        return {"read": read, "insight": insight, "trading_date": trading_date.isoformat(), "cached": False}

    # 2. Generate via LLM (best-effort), else fall back to a numeric read.
    read, insight = _fallback_read(day)
    llm = _get_llm()
    if llm is not None:
        try:
            resp = llm.invoke(
                [
                    SystemMessage(content=_SYSTEM),
                    HumanMessage(content=json.dumps(day)[:6000]),
                ]
            )
            parsed = json.loads((resp.content or "").strip())
            read = (parsed.get("read") or read).strip()
            insight = (parsed.get("insight") or "").strip()
        except Exception:
            logger.exception("coach read generation failed for %s %s", account_id, trading_date)

    # 3. Cache and return.
    try:
        with SessionLocal() as db:
            repo.upsert_daily_read(
                db,
                user_id=user_id,
                account_id=account_id,
                trading_date=trading_date,
                payload={"read": read, "insight": insight},
            )
            db.commit()
    except Exception:
        logger.exception("failed to cache coach read")

    return {"read": read, "insight": insight, "trading_date": trading_date.isoformat(), "cached": False}

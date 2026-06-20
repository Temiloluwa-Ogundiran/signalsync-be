"""
AI domain repository — ALL SQL lives here.

Rules enforced:
- flush() only, never commit().
- No HTTPException, no business recalculation.
- Every raw SQL is parameterized (never f-string values).
- No reserved-word column aliases.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.domains.ai.models import (
    AiChatMessage,
    AiChatSession,
    AiInsight,
    AiMessageRole,
    AiUsage,
    AiUserMemory,
)

# ---------------------------------------------------------------------------
# Shared SQL helper
# ---------------------------------------------------------------------------

def _q(sql: str, account_ids: List[str], extra_params: Optional[dict] = None) -> Tuple[str, dict]:
    """Expand :aids_placeholder → ARRAY[...]::uuid[] with individual bind params."""
    placeholders = ", ".join(f":a{i}" for i in range(len(account_ids)))
    filled = sql.replace(":aids_placeholder", f"ARRAY[{placeholders}]::uuid[]")
    params: dict = {f"a{i}": aid for i, aid in enumerate(account_ids)}
    if extra_params:
        params.update(extra_params)
    return filled, params


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: Optional[str],
    context_type: str = "general",
    context_ref: Optional[str] = None,
    account_id: Optional[uuid.UUID] = None,
) -> AiChatSession:
    now = datetime.now(timezone.utc)
    session = AiChatSession(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        title=title,
        context_type=context_type,
        context_ref=context_ref,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    return session


def get_session(
    db: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[AiChatSession]:
    return (
        db.query(AiChatSession)
        .filter(
            AiChatSession.id == session_id,
            AiChatSession.user_id == user_id,
            AiChatSession.is_deleted.is_(False),
        )
        .first()
    )


def get_context_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    context_type: str,
    context_ref: str,
    account_id: Optional[uuid.UUID],
) -> Optional[AiChatSession]:
    q = db.query(AiChatSession).filter(
        AiChatSession.user_id == user_id,
        AiChatSession.context_type == context_type,
        AiChatSession.context_ref == context_ref,
        AiChatSession.is_deleted.is_(False),
    )
    if account_id:
        q = q.filter(AiChatSession.account_id == account_id)
    return q.first()


def list_sessions(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    cursor_last_message_at: Optional[datetime] = None,
    cursor_id: Optional[uuid.UUID] = None,
) -> List[AiChatSession]:
    q = db.query(AiChatSession).filter(
        AiChatSession.user_id == user_id,
        AiChatSession.is_deleted.is_(False),
    )
    if cursor_last_message_at is not None and cursor_id is not None:
        q = q.filter(
            (AiChatSession.last_message_at < cursor_last_message_at)
            | (
                (AiChatSession.last_message_at == cursor_last_message_at)
                & (AiChatSession.id < cursor_id)
            )
        )
    q = q.order_by(AiChatSession.last_message_at.desc().nullslast(), AiChatSession.id.desc())
    return q.limit(limit).all()


def soft_delete_session(db: Session, *, session: AiChatSession) -> None:
    session.is_deleted = True
    session.updated_at = datetime.now(timezone.utc)
    db.flush()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def create_message(
    db: Session,
    *,
    session_id: uuid.UUID,
    role: AiMessageRole,
    content: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    meta: Optional[dict] = None,
) -> AiChatMessage:
    msg = AiChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role=role,
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        meta=meta,
        created_at=datetime.now(timezone.utc),
    )
    db.add(msg)
    db.flush()
    return msg


def touch_session(db: Session, *, session: AiChatSession, now: datetime) -> None:
    session.last_message_at = now
    session.updated_at = now
    db.flush()


def auto_title_session(db: Session, *, session: AiChatSession, content: str) -> None:
    """Set an immediate placeholder title from the first message.

    Runs synchronously on the first turn so a session is never blank. A nicer
    AI-generated title overwrites this afterwards (see set_session_title), off
    the response path so it adds no latency.
    """
    if session.title is None:
        session.title = content[:80]
        db.flush()


def count_messages(db: Session, *, session_id: uuid.UUID) -> int:
    from sqlalchemy import func, select

    return db.execute(
        select(func.count())
        .select_from(AiChatMessage)
        .where(AiChatMessage.session_id == session_id)
    ).scalar_one()


def set_session_title(
    db: Session, *, session_id: uuid.UUID, user_id: uuid.UUID, title: str
) -> None:
    """Overwrite a session's title (used by the async AI titler)."""
    session = get_session(db, session_id=session_id, user_id=user_id)
    if session is not None:
        session.title = title[:255]
        db.flush()


def get_first_user_message(db: Session, *, session_id: uuid.UUID) -> Optional[str]:
    """The earliest user message text for a session (None if there is none)."""
    from sqlalchemy import select

    return db.execute(
        select(AiChatMessage.content)
        .where(
            AiChatMessage.session_id == session_id,
            AiChatMessage.role == AiMessageRole.user,
        )
        .order_by(AiChatMessage.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

def get_or_create_usage(db: Session, *, user_id: uuid.UUID, period_month: datetime) -> AiUsage:
    usage = db.query(AiUsage).filter(
        AiUsage.user_id == user_id,
        AiUsage.period_month == period_month,
    ).first()
    if usage is None:
        usage = AiUsage(user_id=user_id, period_month=period_month)
        db.add(usage)
        db.flush()
    return usage


def increment_usage(
    db: Session,
    *,
    user_id: uuid.UUID,
    period_month: datetime,
    credits: int,
    input_tokens: int,
    output_tokens: int,
    cost_cents: int = 0,
) -> None:
    stmt = (
        pg_insert(AiUsage)
        .values(
            user_id=user_id,
            period_month=period_month,
            credits_used=credits,
            message_count=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "period_month"],
            set_={
                "credits_used": AiUsage.credits_used + credits,
                "message_count": AiUsage.message_count + 1,
                "input_tokens": AiUsage.input_tokens + input_tokens,
                "output_tokens": AiUsage.output_tokens + output_tokens,
                "cost_cents": AiUsage.cost_cents + cost_cents,
            },
        )
    )
    db.execute(stmt)
    db.flush()


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def get_insights(
    db: Session,
    *,
    user_id: uuid.UUID,
    account_id: Optional[uuid.UUID] = None,
    kind: Optional[str] = None,
) -> List[AiInsight]:
    q = db.query(AiInsight).filter(AiInsight.user_id == user_id)
    if account_id:
        q = q.filter(AiInsight.account_id == account_id)
    if kind:
        q = q.filter(AiInsight.kind == kind)
    return q.order_by(AiInsight.generated_at.desc()).limit(20).all()


def upsert_insight(
    db: Session,
    *,
    user_id: uuid.UUID,
    account_id: Optional[uuid.UUID],
    kind: str,
    payload: dict,
    data_version: int,
    generated_at: datetime,
    valid_until: Optional[datetime] = None,
) -> AiInsight:
    insight = AiInsight(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        kind=kind,
        payload=payload,
        data_version=data_version,
        generated_at=generated_at,
        valid_until=valid_until,
    )
    db.add(insight)
    db.flush()
    return insight


# ---------------------------------------------------------------------------
# User memory
# ---------------------------------------------------------------------------

def get_user_memory(db: Session, *, user_id: uuid.UUID) -> Optional[AiUserMemory]:
    return db.query(AiUserMemory).filter(AiUserMemory.user_id == user_id).first()


def upsert_user_memory(
    db: Session,
    *,
    user_id: uuid.UUID,
    profile: dict,
) -> AiUserMemory:
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(AiUserMemory)
        .values(user_id=user_id, profile=profile, updated_at=now)
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={"profile": profile, "updated_at": now},
        )
        .returning(AiUserMemory)
    )
    result = db.execute(stmt)
    db.flush()
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Account ID lookup (read-only cross-domain query, parameterized)
# ---------------------------------------------------------------------------

def get_account_ids_for_user(db: Session, *, user_id: uuid.UUID) -> List[str]:
    rows = db.execute(
        text("SELECT id FROM trading_accounts WHERE user_id = :uid"),
        {"uid": str(user_id)},
    ).fetchall()
    return [str(r[0]) for r in rows]


def get_accounts_for_user(db: Session, *, user_id: uuid.UUID) -> List[Dict[str, str]]:
    """Return id + human label for every account the user owns. Used to build the AI account map.

    Deletion physically removes the row (no soft-delete), so every remaining row is a
    live account — matching what the user sees from GET /accounts.
    """
    rows = db.execute(
        text(
            "SELECT id, display_name, broker_login FROM trading_accounts "
            "WHERE user_id = :uid"
        ),
        {"uid": str(user_id)},
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "label": r[1] if r[1] else f"Account {r[2]}",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Analytics queries — called by tools/
# Each function opens no session; callers pass `db`.
# ---------------------------------------------------------------------------

def analytics_performance_metrics(
    db: Session,
    account_ids: List[str],
    symbol: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Optional[Any]:
    date_filter = ""
    extra: dict = {}
    if symbol:
        date_filter += " AND UPPER(symbol) = :symbol"
        extra["symbol"] = symbol.upper()
    if from_date:
        date_filter += " AND closed_at >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        date_filter += " AND closed_at <= :to_date"
        extra["to_date"] = to_date

    sql, params = _q(
        f"""
        SELECT
            COUNT(*)                                                                        AS total_trades,
            COUNT(*) FILTER (WHERE net_profit > 0)                                         AS wins,
            COUNT(*) FILTER (WHERE net_profit < 0)                                         AS losses,
            ROUND(COUNT(*) FILTER (WHERE net_profit > 0) * 100.0 / NULLIF(COUNT(*), 0), 1) AS win_rate,
            ROUND(SUM(net_profit)::numeric, 2)                                             AS total_pnl,
            ROUND(AVG(net_profit)::numeric, 2)                                             AS avg_pnl,
            ROUND(AVG(CASE WHEN net_profit > 0 THEN net_profit END)::numeric, 2)           AS avg_win,
            ROUND(AVG(CASE WHEN net_profit < 0 THEN net_profit END)::numeric, 2)           AS avg_loss,
            ROUND(SUM(CASE WHEN net_profit > 0 THEN net_profit ELSE 0 END)::numeric, 2)    AS gross_win,
            ROUND(ABS(SUM(CASE WHEN net_profit < 0 THEN net_profit ELSE 0 END))::numeric, 2) AS gross_loss,
            ROUND(AVG(duration_seconds)::numeric / 60, 1)                                  AS avg_hold_min,
            ROUND(MIN(net_profit)::numeric, 2)                                             AS worst_trade,
            ROUND(MAX(net_profit)::numeric, 2)                                             AS best_trade
        FROM trades
        WHERE account_id = ANY(:aids_placeholder){date_filter}
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchone()


def analytics_breakdown(
    db: Session,
    account_ids: List[str],
    group_expr: str,
    date_filter: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        SELECT
            {group_expr} AS bucket,
            COUNT(*)                                                                        AS trades,
            ROUND(COUNT(*) FILTER (WHERE net_profit > 0) * 100.0 / NULLIF(COUNT(*), 0), 1) AS win_rate,
            ROUND(SUM(net_profit)::numeric, 2)                                             AS total_pnl,
            ROUND(AVG(net_profit)::numeric, 2)                                             AS avg_pnl,
            ROUND(AVG(CASE WHEN net_profit > 0 THEN net_profit END)::numeric, 2)           AS avg_win,
            ROUND(AVG(CASE WHEN net_profit < 0 THEN net_profit END)::numeric, 2)           AS avg_loss
        FROM trades
        WHERE account_id = ANY(:aids_placeholder){date_filter}
        GROUP BY 1
        ORDER BY total_pnl DESC
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()


def analytics_breakdown_duration_scatter(
    db: Session,
    account_ids: List[str],
    date_filter: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        SELECT
            duration_seconds,
            ROUND(net_profit::numeric, 2) AS net_profit,
            symbol,
            direction
        FROM trades
        WHERE account_id = ANY(:aids_placeholder){date_filter}
        ORDER BY duration_seconds ASC
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()


def analytics_period_stats(
    db: Session,
    account_ids: List[str],
    from_date: Any,
    to_date: Any,
) -> Any:
    sql, params = _q(
        """
        SELECT
            COUNT(*)                                                                        AS total_trades,
            ROUND(COUNT(*) FILTER (WHERE net_profit > 0) * 100.0 / NULLIF(COUNT(*), 0), 1) AS win_rate,
            ROUND(SUM(net_profit)::numeric, 2)                                             AS total_pnl,
            ROUND(AVG(net_profit)::numeric, 2)                                             AS avg_pnl,
            ROUND(AVG(CASE WHEN net_profit > 0 THEN net_profit END)::numeric, 2)           AS avg_win,
            ROUND(AVG(CASE WHEN net_profit < 0 THEN net_profit END)::numeric, 2)           AS avg_loss,
            ROUND(SUM(CASE WHEN net_profit > 0 THEN net_profit ELSE 0 END)::numeric, 2)    AS gross_win,
            ROUND(ABS(SUM(CASE WHEN net_profit < 0 THEN net_profit ELSE 0 END))::numeric, 2) AS gross_loss
        FROM trades
        WHERE account_id = ANY(:aids_placeholder)
          AND closed_at >= :from_date
          AND closed_at < :to_date
        """,
        account_ids,
        {"from_date": from_date, "to_date": to_date},
    )
    return db.execute(text(sql), params).fetchone()


def analytics_detect_patterns(
    db: Session,
    account_ids: List[str],
    lookback: Any,
) -> Tuple[List[Any], float]:
    sql, params = _q(
        """
        SELECT net_profit, volume, opened_at, closed_at, DATE(closed_at) AS day
        FROM trades
        WHERE account_id = ANY(:aids_placeholder)
          AND closed_at >= :lookback
        ORDER BY closed_at ASC, id ASC
        """,
        account_ids,
        {"lookback": lookback},
    )
    trades = db.execute(text(sql), params).fetchall()

    sql2, params2 = _q(
        "SELECT ROUND(AVG(volume)::numeric, 2) FROM trades WHERE account_id = ANY(:aids_placeholder)",
        account_ids,
    )
    avg_volume_all = float(db.execute(text(sql2), params2).scalar() or 0)
    return trades, avg_volume_all


def analytics_equity_curve(
    db: Session,
    account_ids: List[str],
    period_expr: str,
    date_filter: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        SELECT
            {period_expr}                              AS period,
            ROUND(SUM(net_profit)::numeric, 2)        AS period_pnl,
            COUNT(*)                                  AS trades,
            COUNT(*) FILTER (WHERE net_profit > 0)    AS wins,
            COUNT(*) FILTER (WHERE net_profit < 0)    AS losses
        FROM trades
        WHERE account_id = ANY(:aids_placeholder){date_filter}
        GROUP BY 1
        ORDER BY 1
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()


def analytics_find_trades(
    db: Session,
    account_ids: List[str],
    filters: str,
    order: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        SELECT
            id, symbol, direction, volume, open_price, close_price,
            sl, tp, net_profit, commission, swap, mfe, mae, setup,
            duration_seconds, session, opened_at, closed_at
        FROM trades
        {filters}
        ORDER BY {order}
        LIMIT :limit
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()


def analytics_streaks(
    db: Session,
    account_ids: List[str],
) -> Tuple[List[Any], List[Any], Any]:
    sql, params = _q(
        "SELECT net_profit FROM trades WHERE account_id = ANY(:aids_placeholder) ORDER BY closed_at ASC, id ASC",
        account_ids,
    )
    trades = db.execute(text(sql), params).fetchall()

    sql2, params2 = _q(
        """
        SELECT DATE(closed_at) AS day, ROUND(SUM(net_profit)::numeric, 2) AS daily_pnl
        FROM trades WHERE account_id = ANY(:aids_placeholder)
        GROUP BY 1 ORDER BY 1
        """,
        account_ids,
    )
    days = db.execute(text(sql2), params2).fetchall()

    sql3, params3 = _q(
        "SELECT balance FROM account_snapshots WHERE account_id = ANY(:aids_placeholder) ORDER BY snapshot_date ASC LIMIT 1",
        account_ids,
    )
    first_balance_row = db.execute(text(sql3), params3).fetchone()
    return trades, days, first_balance_row


def analytics_risk_snapshot(
    db: Session,
    account_ids: List[str],
    today: Any,
    week_ago: Any,
) -> Tuple[List[Any], List[Any], float]:
    sql, params = _q(
        "SELECT net_profit, volume, opened_at, closed_at FROM trades WHERE account_id = ANY(:aids_placeholder) AND closed_at >= :today ORDER BY closed_at",
        account_ids,
        {"today": today},
    )
    today_trades = db.execute(text(sql), params).fetchall()

    sql2, params2 = _q(
        "SELECT net_profit, volume, DATE(closed_at) AS day FROM trades WHERE account_id = ANY(:aids_placeholder) AND closed_at >= :w ORDER BY closed_at",
        account_ids,
        {"w": week_ago},
    )
    week_trades = db.execute(text(sql2), params2).fetchall()

    sql3, params3 = _q(
        "SELECT ROUND(AVG(volume)::numeric, 2) FROM trades WHERE account_id = ANY(:aids_placeholder)",
        account_ids,
    )
    avg_volume = float(db.execute(text(sql3), params3).scalar() or 0)
    return today_trades, week_trades, avg_volume


def analytics_search_journal(
    db: Session,
    account_ids: List[str],
    filters: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        SELECT
            dj.trading_date,
            jm.id          AS message_id,
            jm.message_type,
            jm.content,
            jm.tags,
            jm.created_at
        FROM journal_messages jm
        JOIN daily_journals dj ON dj.id = jm.daily_journal_id
        {filters}
        ORDER BY dj.trading_date DESC, jm.created_at ASC
        LIMIT :limit
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()


def analytics_summarize_journal(
    db: Session,
    account_ids: List[str],
    date_filter: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        SELECT
            dj.trading_date,
            jm.message_type,
            jm.content,
            jm.tags,
            ds.total_pnl,
            ds.trade_count,
            ds.win_count,
            ds.loss_count
        FROM journal_messages jm
        JOIN daily_journals dj ON dj.id = jm.daily_journal_id
        LEFT JOIN (
            SELECT
                t.account_id,
                DATE(timezone(ta.timezone, t.closed_at)) AS trading_date,
                COUNT(*)::int                                   AS trade_count,
                COUNT(*) FILTER (WHERE t.net_profit > 0)::int   AS win_count,
                COUNT(*) FILTER (WHERE t.net_profit < 0)::int   AS loss_count,
                COALESCE(SUM(t.net_profit), 0)                  AS total_pnl
            FROM trades t
            JOIN trading_accounts ta ON ta.id = t.account_id
            WHERE t.account_id = ANY(:aids_placeholder)
            GROUP BY t.account_id, DATE(timezone(ta.timezone, t.closed_at))
        ) ds
            ON ds.account_id = dj.account_id
           AND ds.trading_date = dj.trading_date
        WHERE dj.account_id = ANY(:aids_placeholder)
          AND jm.daily_journal_id IS NOT NULL
          AND jm.message_type IN ('text', 'prompt', 'ai_response')
          AND jm.content IS NOT NULL
          {date_filter}
        ORDER BY dj.trading_date ASC, jm.created_at ASC
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()


def analytics_trade_notes(
    db: Session,
    account_ids: List[str],
    filters: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        SELECT
            t.id          AS trade_id,
            t.symbol,
            t.direction,
            t.volume,
            t.net_profit,
            t.opened_at,
            t.closed_at,
            jm.id         AS message_id,
            jm.message_type,
            jm.content,
            jm.tags,
            jm.created_at AS note_at
        FROM trades t
        JOIN trade_journals tj ON tj.trade_id = t.id
        JOIN journal_messages jm ON jm.trade_journal_id = tj.id
        {filters}
          AND jm.message_type IN ('text', 'prompt', 'ai_response')
          AND jm.content IS NOT NULL
        ORDER BY t.closed_at DESC, jm.created_at ASC
        LIMIT 50
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()


def analytics_tagged_trades(
    db: Session,
    account_ids: List[str],
    date_filter: str,
    extra: dict,
) -> List[Any]:
    sql, params = _q(
        f"""
        WITH tagged AS (
            -- Custom tags (via the trade_tags join).
            SELECT
                tg.name                AS tag,
                grp.name               AS category,
                t.id                   AS trade_id,
                t.net_profit
            FROM trades t
            JOIN trade_tags tt   ON tt.trade_id = t.id
            JOIN tags tg         ON tg.id = tt.tag_id
            JOIN tag_groups grp  ON grp.id = tg.group_id
            WHERE t.account_id = ANY(:aids_placeholder)
              {date_filter}

            UNION ALL

            -- Playbook setups (the trades.setup string column). Treated as a
            -- "Setup" category so "best performing setup" works off the setup
            -- field every trade carries, not only trade_tags.
            SELECT
                t.setup                AS tag,
                'Setup'                AS category,
                t.id                   AS trade_id,
                t.net_profit
            FROM trades t
            WHERE t.account_id = ANY(:aids_placeholder)
              AND t.setup IS NOT NULL
              AND btrim(t.setup) <> ''
              {date_filter}
        )
        SELECT
            category,
            tag,
            COUNT(DISTINCT trade_id)                                                              AS trades,
            COUNT(DISTINCT trade_id) FILTER (WHERE net_profit > 0)                               AS wins,
            ROUND(COUNT(DISTINCT trade_id) FILTER (WHERE net_profit > 0) * 100.0
                  / NULLIF(COUNT(DISTINCT trade_id), 0), 1)                                       AS win_rate,
            ROUND(SUM(net_profit)::numeric, 2)                                                   AS total_pnl,
            ROUND(AVG(net_profit)::numeric, 2)                                                   AS avg_pnl,
            ROUND(SUM(CASE WHEN net_profit > 0 THEN net_profit ELSE 0 END)::numeric, 2)          AS gross_win,
            ROUND(ABS(SUM(CASE WHEN net_profit < 0 THEN net_profit ELSE 0 END))::numeric, 2)     AS gross_loss
        FROM tagged
        GROUP BY category, tag
        ORDER BY total_pnl DESC
        """,
        account_ids,
        extra,
    )
    return db.execute(text(sql), params).fetchall()

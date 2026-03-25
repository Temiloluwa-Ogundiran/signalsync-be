import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session


def delete_for_trading_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
) -> int:
    stmt = text(
        """
        DELETE FROM daily_stats
        WHERE account_id = :account_id
          AND trading_date = :trading_date
        """
    )
    result = db.execute(
        stmt,
        {
            "account_id": str(account_id),
            "trading_date": trading_date,
        },
    )
    return int(result.rowcount or 0)


def rebuild_for_trading_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
    account_timezone: str,
) -> None:
    # trading_date is account-local date. We aggregate trades into that local day.
    stmt = text(
        """
        INSERT INTO daily_stats (
            id,
            account_id,
            trading_date,
            trade_count,
            win_count,
            loss_count,
            total_pnl,
            total_commission,
            gross_win,
            gross_loss,
            best_trade_id,
            worst_trade_id,
            created_at
        )
        SELECT
            gen_random_uuid(),
            :account_id,
            :trading_date,
            COUNT(*)::int,
            COUNT(*) FILTER (WHERE net_profit > 0)::int,
            COUNT(*) FILTER (WHERE net_profit < 0)::int,
            COALESCE(SUM(net_profit), 0),
            COALESCE(SUM(commission), 0),
            COALESCE(SUM(net_profit) FILTER (WHERE net_profit > 0), 0),
            COALESCE(SUM(net_profit) FILTER (WHERE net_profit < 0), 0),
            (
                SELECT t1.id
                FROM trades t1
                WHERE t1.account_id = :account_id
                  AND DATE(timezone(:account_timezone, t1.closed_at)) = :trading_date
                ORDER BY t1.net_profit DESC
                LIMIT 1
            ),
            (
                SELECT t2.id
                FROM trades t2
                WHERE t2.account_id = :account_id
                  AND DATE(timezone(:account_timezone, t2.closed_at)) = :trading_date
                ORDER BY t2.net_profit ASC
                LIMIT 1
            ),
            now()
        FROM trades t
        WHERE t.account_id = :account_id
          AND DATE(timezone(:account_timezone, t.closed_at)) = :trading_date
        ON CONFLICT (account_id, trading_date) DO UPDATE
        SET
            trade_count = EXCLUDED.trade_count,
            win_count = EXCLUDED.win_count,
            loss_count = EXCLUDED.loss_count,
            total_pnl = EXCLUDED.total_pnl,
            total_commission = EXCLUDED.total_commission,
            gross_win = EXCLUDED.gross_win,
            gross_loss = EXCLUDED.gross_loss,
            best_trade_id = EXCLUDED.best_trade_id,
            worst_trade_id = EXCLUDED.worst_trade_id
        """
    )

    db.execute(
        stmt,
        {
            "account_id": str(account_id),
            "trading_date": trading_date,
            "account_timezone": account_timezone,
        },
    )

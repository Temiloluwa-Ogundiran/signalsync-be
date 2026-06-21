"""The Guard watcher — one poll of one account.

Flow per poll: decrypt creds → mt5-core get_open_positions → build Tick → run the
engine → persist state/daily-results/ticks → fire email alerts on tier escalation.

The engine is pure; this module is the only place that touches the network and the
clock. RULES.md EXCEPTION: Guard is deliberately allowed to poll on a schedule (a
watchdog that only checks on-demand is useless). Do not "fix" this to on-demand.
"""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.domains.accounts.mt5_core_client import Mt5CoreClient
from app.domains.accounts import repository as account_repo
from app.shared.utils.encryption import decrypt_secret

from . import alerts as guard_alerts
from . import repository as guard_repo
from .account import GuardConfig
from .engine import Engine
from .engine.core import EngineMemory
from .engine.dayclock import firm_day_key
from .models import GuardAccount, GuardConnectionHealth
from .service import build_config
from .tick import Position, Tick
from .views import monitor_view

logger = logging.getLogger(__name__)


# -- EngineMemory <-> JSON ----------------------------------------------------

def _memory_to_json(mem: EngineMemory) -> Dict[str, Any]:
    return {
        "day_key": mem.day_key,
        "day_anchor": str(mem.day_anchor),
        "day_start_equity": str(mem.day_start_equity),
        "peak": str(mem.peak),
        "dd_floor_locked": mem.dd_floor_locked,
        "daily_results": {k: str(v) for k, v in mem.daily_results.items()},
        "trading_days": sorted(mem.trading_days),
        "traded_today_seen": mem.traded_today_seen,
    }


def _memory_from_json(data: Dict[str, Any]) -> EngineMemory:
    return EngineMemory(
        day_key=data.get("day_key"),
        day_anchor=Decimal(data.get("day_anchor", "0")),
        day_start_equity=Decimal(data.get("day_start_equity", "0")),
        peak=Decimal(data.get("peak", "0")),
        dd_floor_locked=bool(data.get("dd_floor_locked", False)),
        daily_results={k: Decimal(v) for k, v in data.get("daily_results", {}).items()},
        trading_days=set(data.get("trading_days", [])),
        traded_today_seen=bool(data.get("traded_today_seen", False)),
    )


def _load_memory(db: Session, guard: GuardAccount, engine: Engine) -> EngineMemory:
    state = guard_repo.get_state(db, guard.id)
    if state is not None and state.memory_json:
        return _memory_from_json(state.memory_json)
    return engine.init_memory(opening_balance=guard.size)


# -- mt5-core poll ------------------------------------------------------------

def _fetch_account_snapshot(guard: GuardAccount, db: Session) -> Dict[str, Any]:
    """Synchronously fetch the account equity/balance/positions from mt5-core."""
    import asyncio

    account = account_repo.get_account_by_id(db, guard.trading_account_id)
    if account is None:
        raise RuntimeError("linked trading account no longer exists")
    password = decrypt_secret(account.encrypted_investor_password)
    client = Mt5CoreClient()
    credentials = {
        "login": account.broker_login,
        "password": password,
        "server": account.broker_server,
        "broker": account.broker_name,
    }
    return asyncio.run(client.get_open_positions(credentials=credentials))


def _build_tick(snapshot: Dict[str, Any], now: datetime) -> Tick:
    equity = Decimal(str(snapshot.get("equity", "0")))
    balance = Decimal(str(snapshot.get("balance", "0")))
    raw_positions: List[Dict[str, Any]] = snapshot.get("positions", []) or []
    positions = [
        Position.of(
            ticket=p.get("ticket", ""),
            symbol=p.get("symbol", ""),
            volume=p.get("volume", 0),
            open_price=p.get("open_price", p.get("price_open", 0)),
            profit=p.get("profit", 0),
        )
        for p in raw_positions
    ]
    # Realized day P&L = balance - day-start balance is computed by the engine via
    # anchors; here we pass floating context. day_realized_pnl/traded_today come from
    # the snapshot when mt5-core provides them, else conservative defaults.
    return Tick.of(
        ts=now,
        balance=balance,
        equity=equity,
        positions=positions,
        traded_today=bool(snapshot.get("traded_today", len(positions) > 0)),
        day_realized_pnl=Decimal(str(snapshot.get("day_realized_pnl", "0"))),
    )


# -- the poll -----------------------------------------------------------------

def run_one_poll(db: Session, guard: GuardAccount) -> Optional[str]:
    """Run a single poll. Returns the resulting status string, or None on failure.

    Caller (the Celery task) owns the commit.
    """
    config = build_config(guard)
    engine = Engine(config)
    now = datetime.now(timezone.utc)

    try:
        snapshot = _fetch_account_snapshot(guard, db)
    except Exception as exc:  # noqa: BLE001 - any failure must alert, never crash silently
        _handle_offline(db, guard, exc)
        return None

    # Recovered from a prior offline state.
    if guard.connection_health != GuardConnectionHealth.ok.value:
        guard.connection_health = GuardConnectionHealth.ok.value
        guard.poll_error_message = None
    guard.consecutive_poll_failures = 0

    mem = _load_memory(db, guard, engine)
    tick = _build_tick(snapshot, now)
    result = engine.process(mem, tick)
    state = result.state

    _persist(db, guard, engine, result, tick, now)
    guard.last_polled_at = now

    # Alerts on tier escalation (de-duped against guard.last_alert_tier).
    guard_alerts.maybe_alert(db, guard, state)
    return state.status.value


def _persist(db: Session, guard: GuardAccount, engine: Engine, result, tick: Tick,
             now: datetime) -> None:
    state = result.state
    mem = result.memory
    view = monitor_view(state)
    # Carry the open positions (read-only) into the persisted snapshot so the API
    # can serve the whole dashboard from one row.
    view["positions"] = [
        {
            "ticket": p.ticket, "symbol": p.symbol,
            "volume": float(p.volume), "open_price": float(p.open_price),
            "profit": float(p.profit),
        }
        for p in tick.positions
    ]

    guard_repo.upsert_state(
        db,
        guard_id=guard.id,
        ts=state.ts,
        equity=state.equity,
        balance=state.balance,
        peak=state.peak,
        day_anchor=state.day_anchor,
        status=state.status.value,
        buffers_json=view,
        challenge_json=state.challenge.to_dict(),
        memory_json=_memory_to_json(mem),
    )

    # Closed-day result for the current firm-day (feeds consistency + min-days).
    dl = engine.spec.daily_loss
    key = firm_day_key(now, dl.reset_hour, dl.reset_tz)
    day_pnl = mem.daily_results.get(key, Decimal(0))
    guard_repo.upsert_daily_result(
        db,
        guard_id=guard.id,
        result_date=date.fromisoformat(key),
        pnl=day_pnl,
        trade_count=0,
        is_trading_day=key in mem.trading_days,
    )

    # Rolling tick window for the chart.
    guard_repo.append_tick(db, guard_id=guard.id, ts=state.ts, equity=state.equity)
    guard_repo.prune_ticks(db, guard.id)


def _handle_offline(db: Session, guard: GuardAccount, exc: Exception) -> None:
    """A failed poll is the worst moment to be blind — alert once, never silently."""
    was_ok = guard.connection_health == GuardConnectionHealth.ok.value
    guard.connection_health = GuardConnectionHealth.offline.value
    guard.poll_error_message = str(exc)[:500]
    guard.consecutive_poll_failures = (guard.consecutive_poll_failures or 0) + 1
    logger.warning("guard poll failed for %s: %s", guard.id, exc)
    if was_ok:
        guard_alerts.alert_offline(db, guard)

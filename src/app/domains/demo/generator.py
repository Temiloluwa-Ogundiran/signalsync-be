"""Deterministic demo-trade generator.

Pure (no DB): given a seed, produce a realistic, internally-consistent ~4-month
history (mid-July → end of October 2025) for a personal-funds forex day-trading
account, ending with a strong cumulative run (~$8k on $25k). Each calendar month
has 11–15 active trading days; most days run 1–4 trades, with 1–2 high-volume
"scaling" days reaching 10–14 independent tickets (same symbol/direction,
clustered entries and exits — a manual scale-in, not linked positions).
Behavioral leaks are planted on purpose so the coach has real material.

Everything a TradeSpec carries is computed so downstream analytics reconcile:
lots are derived from risk ÷ stop, net_profit = profit + commission + swap, etc.
The same seed always yields the same history (idempotent re-runs); the caller
varies the seed per user.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

# ── Instrument model ────────────────────────────────────────────────────────
# pip_size: price increment of one pip. pip_value_per_lot: $ per pip for 1.00 lot
# (standard 100k contract). price_range: plausible spot band. typical_stop_pips:
# realistic stop distance band per instrument volatility.


@dataclass(frozen=True)
class Instrument:
    symbol: str
    pip_size: float
    pip_value_per_lot: float  # USD per pip for 1.0 lot
    price_lo: float
    price_hi: float
    stop_lo: float  # min stop distance in pips
    stop_hi: float  # max stop distance in pips
    digits: int
    weight: float  # selection weight


INSTRUMENTS = [
    Instrument("EURUSD", 0.0001, 10.0, 1.0700, 1.1000, 8, 18, 5, 0.30),
    Instrument("GBPUSD", 0.0001, 10.0, 1.2500, 1.2900, 10, 22, 5, 0.24),
    Instrument("GBPJPY", 0.01, 6.7, 188.0, 198.0, 14, 35, 3, 0.18),
    Instrument("XAUUSD", 0.1, 1.0, 2300.0, 2400.0, 30, 80, 2, 0.16),
    Instrument("USDJPY", 0.01, 6.7, 148.0, 158.0, 10, 22, 3, 0.12),
]

# Playbook setups. The last two are the "bad" ones the leak days use.
SETUP_LONDON_BREAKOUT = "London breakout"
SETUP_NY_REVERSAL = "NY reversal"
SETUP_TREND_PULLBACK = "trend pullback"
SETUP_RANGE_FADE = "range fade"
SETUP_NO_SETUP = "no setup"
SETUP_REVENGE = "revenge"

GOOD_SETUPS = [
    SETUP_LONDON_BREAKOUT,
    SETUP_NY_REVERSAL,
    SETUP_TREND_PULLBACK,
    SETUP_RANGE_FADE,
]

# Session windows in UTC (hour ranges). London 07-11, overlap 12-15, NY 13-16.
SESSION_WINDOWS = {
    "london": (7, 11),
    "london_ny_overlap": (12, 15),
    "new_york": (13, 16),
}


@dataclass
class TradeSpec:
    symbol: str
    direction: str  # "buy" | "sell"
    open_time: datetime  # tz-aware UTC
    close_time: datetime
    open_price: Decimal
    close_price: Decimal
    lots: Decimal
    sl: Decimal
    tp: Decimal
    commission: Decimal
    swap: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    position_id: str
    session: str
    duration_seconds: int
    setup: str
    plan_followed: bool
    realized_r: float  # for verification/notes
    pip_value_per_lot: float


@dataclass
class DaySpec:
    day: date
    trades: list[TradeSpec] = field(default_factory=list)
    discipline_score: int = 0  # 1..10
    note_html: str | None = None
    mood: str | None = None  # good | neutral | bad
    journaled: bool = False
    label: str = "normal"  # tilt | chase | model | overtrade | clean | normal


@dataclass
class DemoData:
    starting_balance: Decimal
    days: list[DaySpec]

    @property
    def trades(self) -> list[TradeSpec]:
        return [t for d in self.days for t in d.trades]


def _money(x: float) -> Decimal:
    return Decimal(str(round(x, 2))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _price(x: float, digits: int) -> Decimal:
    q = Decimal(10) ** -digits
    return Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP)


def _lots(risk_dollars: float, stop_pips: float, pip_value_per_lot: float) -> Decimal:
    """lots = risk ÷ (stop_pips × pip_value_per_lot), clamped to a sane band."""
    raw = risk_dollars / (stop_pips * pip_value_per_lot)
    raw = max(0.01, min(raw, 2.5))
    return Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pick_instrument(rng: random.Random) -> Instrument:
    return rng.choices(INSTRUMENTS, weights=[i.weight for i in INSTRUMENTS], k=1)[0]


def _session_for_hour(hour_utc: int) -> str:
    if 12 <= hour_utc <= 15:
        return "london_ny_overlap"
    if 7 <= hour_utc <= 11:
        return "london"
    if 13 <= hour_utc <= 16:
        return "new_york"
    return "off_hours"


def _open_dt(rng: random.Random, day: date, session: str) -> datetime:
    lo, hi = SESSION_WINDOWS[session]
    hour = rng.randint(lo, hi - 1)
    minute = rng.randint(0, 59)
    return datetime.combine(day, time(hour, minute, tzinfo=timezone.utc))


def _build_trade(
    rng: random.Random,
    *,
    day: date,
    balance: float,
    setup: str,
    plan_followed: bool,
    forced_outcome: str | None = None,  # "win" | "loss" | None (random)
    risk_pct: float | None = None,
    session: str | None = None,
    instrument: Instrument | None = None,
    hold_minutes: int | None = None,
) -> TradeSpec:
    inst = instrument or _pick_instrument(rng)
    session = session or rng.choices(
        list(SESSION_WINDOWS.keys()), weights=[0.45, 0.35, 0.20], k=1
    )[0]
    direction = rng.choice(["buy", "sell"])

    # Risk: 0.5%–1% of balance unless overridden (tilt uses more).
    rpct = risk_pct if risk_pct is not None else rng.uniform(0.005, 0.01)
    risk_dollars = balance * rpct

    stop_pips = rng.uniform(inst.stop_lo, inst.stop_hi)
    lots = _lots(risk_dollars, stop_pips, inst.pip_value_per_lot)

    open_price_f = rng.uniform(inst.price_lo, inst.price_hi)

    # Outcome: ~50% win, but winners pay more R than losers cost.
    if forced_outcome == "win":
        is_win = True
    elif forced_outcome == "loss":
        is_win = False
    else:
        is_win = rng.random() < 0.50

    if is_win:
        realized_r = rng.uniform(1.3, 2.6)  # let winners run
    else:
        realized_r = -rng.uniform(0.7, 1.05)  # cut losers near 1R

    # Price move in pips = realized_r × stop_pips (sign by direction & R sign).
    move_pips = realized_r * stop_pips
    sign = 1 if direction == "buy" else -1
    close_price_f = open_price_f + sign * move_pips * inst.pip_size

    # SL/TP from stop distance (TP at the win target ~ +2R for the plan).
    if direction == "buy":
        sl_f = open_price_f - stop_pips * inst.pip_size
        tp_f = open_price_f + 2.0 * stop_pips * inst.pip_size
    else:
        sl_f = open_price_f + stop_pips * inst.pip_size
        tp_f = open_price_f - 2.0 * stop_pips * inst.pip_size

    # Gross P&L from price move: pips_moved (in favour) × pip_value × lots.
    pips_pl = (close_price_f - open_price_f) / inst.pip_size * sign
    gross = pips_pl * inst.pip_value_per_lot * float(lots)

    commission = -abs(float(lots) * 3.5)  # ~$3.5/lot round-turn
    open_dt = _open_dt(rng, day, session)
    hold_min = hold_minutes if hold_minutes is not None else rng.randint(8, 180)
    close_dt = open_dt + timedelta(minutes=hold_min)
    held_overnight = close_dt.date() > open_dt.date()
    swap = -abs(float(lots) * rng.uniform(1.0, 3.0)) if held_overnight else 0.0

    net = gross + commission + swap

    return TradeSpec(
        symbol=inst.symbol,
        direction=direction,
        open_time=open_dt,
        close_time=close_dt,
        open_price=_price(open_price_f, inst.digits),
        close_price=_price(close_price_f, inst.digits),
        lots=lots,
        sl=_price(sl_f, inst.digits),
        tp=_price(tp_f, inst.digits),
        commission=_money(commission),
        swap=_money(swap),
        gross_profit=_money(gross),
        net_profit=_money(net),
        position_id=str(rng.randint(10_000_000, 99_999_999)),
        session=_session_for_hour(open_dt.hour),
        duration_seconds=hold_min * 60,
        setup=setup,
        plan_followed=plan_followed,
        realized_r=round(realized_r, 2),
        pip_value_per_lot=inst.pip_value_per_lot,
    )


# ── Day builders for the planted behavioral leaks ───────────────────────────


def _re_enter(rng, base: TradeSpec, day, balance, *, minutes_after, lots_mult,
              setup, plan_followed, forced_outcome) -> TradeSpec:
    """A fast same-symbol re-entry after `base`, at scaled lots — the core leak."""
    inst = next(i for i in INSTRUMENTS if i.symbol == base.symbol)
    t = _build_trade(
        rng, day=day, balance=balance, setup=setup, plan_followed=plan_followed,
        forced_outcome=forced_outcome, instrument=inst,
    )
    # Anchor it right after the base trade closed, on the same symbol.
    open_dt = base.close_time + timedelta(minutes=minutes_after)
    hold = max(5, t.duration_seconds // 60)
    t.open_time = open_dt
    t.close_time = open_dt + timedelta(minutes=hold)
    t.session = _session_for_hour(open_dt.hour)
    t.lots = (base.lots * Decimal(str(lots_mult))).quantize(Decimal("0.01"))
    # Recompute P&L for the scaled lots from its realized R.
    move_pips = t.realized_r * ((float(base.lots) and 1) or 1)  # keep R sign
    gross = t.realized_r * abs(float(t.sl - t.open_price)) / inst.pip_size \
        * inst.pip_value_per_lot * float(t.lots)
    commission = -abs(float(t.lots) * 3.5)
    t.commission = _money(commission)
    t.gross_profit = _money(gross)
    t.net_profit = _money(gross + commission + float(t.swap))
    return t


def _tilt_day(rng, day, balance) -> DaySpec:
    """Loss → fast same-symbol re-entry at increased lots → another loss."""
    inst = _pick_instrument(rng)
    t1 = _build_trade(rng, day=day, balance=balance, setup=SETUP_NY_REVERSAL,
                      plan_followed=True, forced_outcome="loss", instrument=inst)
    t2 = _re_enter(rng, t1, day, balance, minutes_after=rng.randint(2, 5),
                   lots_mult=rng.uniform(1.8, 2.5), setup=SETUP_REVENGE,
                   plan_followed=False, forced_outcome="loss")
    return DaySpec(
        day=day, trades=[t1, t2], discipline_score=3, label="tilt", mood="bad",
        journaled=True,
        note_html=(
            "<p>Took the first loss fine, then jumped right back into "
            f"{inst.symbol} bigger to win it back. Broke my rule. Turned a "
            "small red day into a worse one. This keeps happening.</p>"
        ),
    )


def _chase_day(rng, day, balance) -> DaySpec:
    """Clean A+ winner, then a no-setup re-entry minutes later that gives back."""
    inst = _pick_instrument(rng)
    t1 = _build_trade(rng, day=day, balance=balance, setup=SETUP_LONDON_BREAKOUT,
                      plan_followed=True, forced_outcome="win", instrument=inst)
    t2 = _re_enter(rng, t1, day, balance, minutes_after=rng.randint(3, 8),
                   lots_mult=rng.uniform(0.8, 1.2), setup=SETUP_NO_SETUP,
                   plan_followed=False, forced_outcome="loss")
    return DaySpec(
        day=day, trades=[t1, t2], discipline_score=6, label="chase", mood="neutral",
        journaled=True,
        note_html=(
            "<p>Great breakout, banked it. Then forced another entry on "
            f"{inst.symbol} with no setup and gave a chunk back. Should have "
            "been done for the day.</p>"
        ),
    )


def _model_day(rng, day, balance) -> DaySpec:
    """All trades match a setup, stops respected, stopped while ahead."""
    n = rng.randint(2, 3)
    trades = []
    for _ in range(n):
        trades.append(_build_trade(
            rng, day=day, balance=balance, setup=rng.choice(GOOD_SETUPS),
            plan_followed=True,
            forced_outcome="win" if rng.random() < 0.6 else "loss",
        ))
    trades.sort(key=lambda t: t.open_time)
    return DaySpec(
        day=day, trades=trades, discipline_score=8, label="model", mood="good",
        journaled=True,
        note_html=(
            "<p>Textbook day. Every entry was a real setup, stops honoured, "
            "stopped once I was green. This is the model.</p>"
        ),
    )


def _overtrade_day(rng, day, balance) -> DaySpec:
    """6+ small trades, near-flat net after costs."""
    n = rng.randint(6, 9)
    trades = []
    for _ in range(n):
        trades.append(_build_trade(
            rng, day=day, balance=balance,
            setup=rng.choice(GOOD_SETUPS + [SETUP_NO_SETUP]),
            plan_followed=rng.random() < 0.5,
            risk_pct=rng.uniform(0.003, 0.006),  # smaller size, choppy
        ))
    trades.sort(key=lambda t: t.open_time)
    return DaySpec(day=day, trades=trades, discipline_score=5, label="overtrade",
                   mood="neutral")


def _clean_day(rng, day, balance) -> DaySpec:
    """A single disciplined trade."""
    t = _build_trade(rng, day=day, balance=balance, setup=rng.choice(GOOD_SETUPS),
                     plan_followed=True)
    return DaySpec(day=day, trades=[t], discipline_score=rng.randint(6, 8),
                   label="clean")


def _scaled_cluster(rng, day, balance, *, instrument, direction, setup,
                    n, base_open_dt, outcome) -> list[TradeSpec]:
    """N independent trades on one symbol+direction, clustered in time.

    A trader scaling into a position fires several tickets within a few minutes
    at slightly different prices, then exits them around the same time. They are
    NOT linked — each is its own trade with its own position_id and P&L — they
    just share the symbol, direction, and a tight entry/exit window.
    """
    trades: list[TradeSpec] = []
    # All tickets aim at the same outcome (the position works or it doesn't).
    exit_anchor_min = rng.randint(25, 120)  # minutes after the first entry
    for k in range(n):
        t = _build_trade(
            rng, day=day, balance=balance, setup=setup, plan_followed=True,
            forced_outcome=outcome, instrument=instrument,
            risk_pct=rng.uniform(0.003, 0.006),  # each tranche risks less
        )
        # Force same direction and cluster the entry/exit times.
        t.direction = direction
        entry = base_open_dt + timedelta(minutes=k * rng.randint(1, 4))
        exit_dt = base_open_dt + timedelta(
            minutes=exit_anchor_min + rng.randint(-4, 4)
        )
        if exit_dt <= entry:
            exit_dt = entry + timedelta(minutes=rng.randint(5, 15))
        t.open_time = entry
        t.close_time = exit_dt
        t.session = _session_for_hour(entry.hour)
        t.duration_seconds = int((exit_dt - entry).total_seconds())
        trades.append(t)
    return trades


def _scaling_day(rng, day, balance) -> DaySpec:
    """A high-volume conviction day: 10–14 tickets from scaling into positions.

    Built as one big scale-in (and sometimes a second smaller cluster) so the
    ticket count is high but coherent — same symbol/direction, clustered times.
    """
    total = rng.randint(10, 14)
    direction = rng.choice(["buy", "sell"])
    inst = _pick_instrument(rng)
    setup = rng.choice(GOOD_SETUPS)
    outcome = "win" if rng.random() < 0.6 else "loss"

    # Most tickets in the primary scale; optionally a second cluster.
    if rng.random() < 0.5:
        primary_n = total
        clusters = [(inst, direction, setup, primary_n, outcome)]
    else:
        primary_n = rng.randint(6, total - 4)
        inst2 = _pick_instrument(rng)
        dir2 = rng.choice(["buy", "sell"])
        out2 = "win" if rng.random() < 0.6 else "loss"
        clusters = [
            (inst, direction, setup, primary_n, outcome),
            (inst2, dir2, rng.choice(GOOD_SETUPS), total - primary_n, out2),
        ]

    trades: list[TradeSpec] = []
    base = _open_dt(rng, day, rng.choice(list(SESSION_WINDOWS.keys())))
    for ci, (cinst, cdir, cset, cn, cout) in enumerate(clusters):
        cbase = base + timedelta(hours=ci * rng.randint(1, 3))
        trades.extend(_scaled_cluster(
            rng, day, balance, instrument=cinst, direction=cdir, setup=cset,
            n=cn, base_open_dt=cbase, outcome=cout,
        ))
    trades.sort(key=lambda t: t.open_time)
    return DaySpec(day=day, trades=trades, discipline_score=rng.randint(6, 7),
                   label="scaling")


def _normal_day(rng, day, balance) -> DaySpec:
    n = rng.randint(1, 4)
    trades = []
    for _ in range(n):
        plan = rng.random() < 0.8
        trades.append(_build_trade(
            rng, day=day, balance=balance,
            setup=rng.choice(GOOD_SETUPS) if plan else SETUP_NO_SETUP,
            plan_followed=plan,
        ))
    trades.sort(key=lambda t: t.open_time)
    disc = 7 if all(t.plan_followed for t in trades) else 6
    return DaySpec(day=day, trades=trades, discipline_score=disc, label="normal")


# Per calendar month, how many days are actually traded. Floor of 11 so every
# month looks active; some months run hot at 14–15.
MIN_TRADING_DAYS_PER_MONTH = 11
MAX_TRADING_DAYS_PER_MONTH = 15


def _trading_days(end: date, weeks: int, rng: random.Random) -> list[date]:
    """Active trading-day dates over the window ending at `end`.

    Selection is per calendar month: each month gets 11–15 active days (capped at
    the weekdays it actually contains), spread across the month, leaving the rest
    as no-trade days. This guarantees every month reads as active.
    """
    start = end - timedelta(weeks=weeks)

    # Bucket weekdays by (year, month).
    by_month: dict[tuple[int, int], list[date]] = {}
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            by_month.setdefault((d.year, d.month), []).append(d)
        d += timedelta(days=1)

    active: list[date] = []
    for _key, weekdays in by_month.items():
        target = rng.randint(MIN_TRADING_DAYS_PER_MONTH, MAX_TRADING_DAYS_PER_MONTH)
        target = min(target, len(weekdays))
        active.extend(rng.sample(weekdays, target))

    active.sort()
    return active


# Fixed end of the demo track record (a personal-funds account's history that
# ends in late October 2025 — not anchored to signup, so it reads as a real
# past stretch). Last full trading week of Oct 2025.
DEFAULT_END_DATE = date(2025, 10, 31)
# Personal account (not a prop eval): target a strong winning run of ~$8k.
DEFAULT_TARGET_NET = 8_000.0


def generate_demo_data(
    *,
    seed: int,
    signup_date: date | None = None,  # kept for call compatibility; unused
    end_date: date = DEFAULT_END_DATE,
    starting_balance: float = 25_000.0,
    weeks: int = 16,
    target_net: float = DEFAULT_TARGET_NET,
) -> DemoData:
    """Generate a deterministic ~4-month personal-account history for one account.

    Spread across ~16 weeks ending `end_date`; realistic equity that drifts up
    with drawdowns to a cumulative ~`target_net`. Behavioral leaks are planted so
    the coach has material; this is a personal account so prop daily/drawdown
    caps do not apply.
    """
    rng = random.Random(seed)
    weekdays = _trading_days(end_date, weeks, rng)

    # Choose which special-leak days to place (spread across the window).
    n = len(weekdays)
    if n < 6:
        weeks += 2
        weekdays = _trading_days(end_date, weeks, rng)
        n = len(weekdays)

    # Reserve distinct indices for the planted days. Includes 1–2 high-volume
    # "scaling" conviction days (10–14 tickets from scaling into positions).
    idxs = list(range(n))
    rng.shuffle(idxs)
    special = {}
    planted = ["tilt", "chase", "model", "overtrade", "scaling"]
    if rng.random() < 0.5:
        planted.append("scaling")  # sometimes a second heavy day
    for label in planted:
        if idxs:
            special[idxs.pop()] = label

    balance = starting_balance
    days: list[DaySpec] = []

    builders = {
        "tilt": _tilt_day,
        "chase": _chase_day,
        "model": _model_day,
        "overtrade": _overtrade_day,
        "scaling": _scaling_day,
    }

    for i, day in enumerate(weekdays):
        label = special.get(i)
        if label:
            spec = builders[label](rng, day, balance)
        else:
            # Mostly normal days, a few clean single-trade days.
            spec = _clean_day(rng, day, balance) if rng.random() < 0.3 \
                else _normal_day(rng, day, balance)

        # Personal account (not a prop eval): allow real losing days, but cap the
        # worst at ~6% of balance so the curve has drawdowns without a blowup.
        day_net = sum(float(t.net_profit) for t in spec.trades)
        max_day_loss = -0.06 * balance
        if day_net < max_day_loss and spec.trades:
            scale = max_day_loss / day_net
            for t in spec.trades:
                if float(t.net_profit) < 0:
                    t.net_profit = _money(float(t.net_profit) * scale)
                    t.gross_profit = _money(
                        float(t.net_profit) - float(t.commission) - float(t.swap)
                    )
            day_net = sum(float(t.net_profit) for t in spec.trades)

        balance += day_net
        days.append(spec)

    data = DemoData(starting_balance=_money(starting_balance), days=days)
    return _retune_to_target(data, rng, target_net=target_net)


def _retune_to_target(
    data: DemoData, rng: random.Random, *, target_net: float
) -> DemoData:
    """Scale winners so the cumulative net lands near `target_net`.

    Only winning trades are scaled (up or down); losers — the coachable material
    — are left intact, so win rate, the leak days, and the drawdown shape all
    survive. A ±15% tolerance avoids fiddling when it's already close.
    """
    net = sum(float(t.net_profit) for t in data.trades)
    winners = [t for t in data.trades if float(t.net_profit) > 0]
    win_sum = sum(float(t.net_profit) for t in winners)
    loss_sum = net - win_sum  # negative

    if win_sum <= 0:
        return data
    if abs(net - target_net) <= 0.15 * target_net:
        return data

    # net = win_sum + loss_sum ; want win_sum' + loss_sum = target_net
    needed_win = target_net - loss_sum
    factor = max(0.1, needed_win / win_sum)
    for t in winners:
        t.net_profit = _money(float(t.net_profit) * factor)
        t.gross_profit = _money(
            float(t.net_profit) - float(t.commission) - float(t.swap)
        )
    return data

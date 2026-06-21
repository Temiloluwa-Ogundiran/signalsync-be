# Partna Guard — v1 Spec (Awareness Rail)

**Status:** Draft for review. No code written yet.
**Scope:** v1 = read-only awareness + email alerts. No enforcement.

---

## 0. The one line

**Get funded, stay funded.** A read-only, always-on watchdog for prop-challenge traders that shows
the live **distance to each breach line**, tracks pass progress, and emails the trader before they
breach. We tap them on the shoulder; they decide. Guard never closes a trade in v1.

Partna Guard is a **new top-level rail** in TradePartna (alongside AI and Copy-Trading), not a
standalone product. It coaches *during* the challenge; TradePartna journals *after* the trade.

---

## 1. Locked v1 decisions

| Area | Decision |
|---|---|
| **Enforcement** | ❌ None. Awareness + alerts only. (Lowest liability, fastest, proves demand.) |
| **Firms** | ❌ No curated firm library. The user enters their firm's rules per account. |
| **Stress harness** | ❌ Cut. Only basic unit tests on the buffer math. |
| **Watcher** | Poll mt5-core on a tight interval — a deliberate, documented exception to RULES.md's no-scheduled-sync rule, scoped to Guard. |
| **Rules captured** | Daily loss %, Max DD % (static/trailing), Profit target % **+ consistency cap % + min trading days**. |
| **Alerts** | **Email** (Resend) for v1. Telegram + Push shown in UI but disabled / "coming soon". |
| **Headline** | **Distance to the line** on the Monitor dashboard. Pass plan is a secondary panel. |
| **Account link** | **Reuse existing connected `TradingAccount`s.** No new credential entry. |
| **Watcher scope** | **Always-on while Guard is enabled** on the account. |
| **Surface** | New top-level FE nav rail, feature-flagged (default off until BE is live). |

---

## 2. Architecture — one engine, three views

The whole product is a **deterministic rules engine** that emits one `AccountState` object per poll.
Every surface (and a future LLM coach) is a read-only projection of that object. The engine never
acts and never reads the clock — it is a pure function of `(rule_spec, memory, tick)`. This is what
makes the math testable and the surfaces trivial.

```
Existing TradingAccount ──(user enables Guard + enters firm rules)──▶ GuardAccount
        │
        ▼
Guard watcher (Celery loop, polls mt5-core every N sec while enabled)
        │  poll → Tick(equity, balance, positions, day_realized_pnl)
        ▼
Rules Engine  ← ported from the prototype: pure, Decimal-exact, no I/O
        │  emits ONE AccountState per poll
        ├──▶ guard_state          (latest snapshot, upsert)
        ├──▶ guard_daily_results  (closed-day P&L → consistency + min-days)
        ├──▶ guard_tick_window    (short rolling window → the intraday equity chart)
        ├──▶ Email dispatcher      (fire on tier ESCALATION only, de-duped) — Resend
        └──▶ /guard/* read-models ──▶ FE Guard rail
                                       ├─ /guard        Awareness dashboard (headline)
                                       └─ /guard/rules  Firm rules form + contract
```

**Reused from prod:** mt5-core client (`get_open_positions`), Celery/Redis, Resend email, accounts
domain, notifications, FE proxy/auth, nav-registry, TanStack Query, recharts.

**Ported from the prototype** (`partna guard/src/partna_guard/`): `engine/core.py`, `engine/dayclock.py`,
`spec.py`, `state.py`, `money.py`, `enums.py`, `tick.py`, and the `monitor_view` / `copilot_view` /
`rules_view` read-models. The engine is already pure and `Decimal`-exact — port nearly verbatim.

**Dropped from the prototype:** `specs/` (curated firms), `harness.py` (stress paths), `intervention/`
(flatten/lock), the allowlist, and the firm registry.

**The one math note that matters:** `FirmRuleSpec` is now **hydrated from the user's `rule_spec_json`**,
not a fixture. The Python engine remains the single source of truth for floor math (firm reset clock,
balance-vs-equity basis, static-vs-trailing DD). The FE renders the emitted `AccountState`; it must
**not** recompute floors (the awareness-dashboard.jsx prototype hardcodes simplified static math — that
was a mock; the real numbers come from the API).

---

## 3. The rules engine (the moat)

Ported from `partna guard/src/partna_guard/engine/core.py`. Pure function, no I/O, no clock reads, no
randomness. `Decimal` end to end, crossing to `float` only at the response boundary (per RULES.md).

### Inputs

**`FirmRuleSpec`** (hydrated from `guard_accounts.rule_spec_json`):

| Field | Meaning |
|---|---|
| `daily_loss.pct` | fraction, e.g. `0.05` |
| `daily_loss.basis` | `BALANCE` \| `EQUITY` |
| `daily_loss.anchor` | `DAY_START_BALANCE` \| `HIGHER_OF_BALANCE_EQUITY` |
| `daily_loss.reset_hour` + `reset_tz` | the **firm's** clock, stored explicitly (e.g. `0`, `"Europe/Prague"`) |
| `max_drawdown.pct` | fraction, e.g. `0.10` |
| `max_drawdown.type` | `STATIC` \| `TRAILING` |
| `max_drawdown.anchor_ref` | `INITIAL_BALANCE` \| `PEAK_EQUITY` \| `PEAK_BALANCE` |
| `max_drawdown.locks_at_initial` | trailing-then-lock latch |
| `profit_target.pct` | fraction, e.g. `0.08` |
| `min_days.count` + `day_counts_if` | `ANY_TRADE` \| `RESULT_MOVES_X` |
| `consistency.cap` + `basis` | `TOTAL_PROFIT` \| `TARGET` |

`size` (account starting balance) is supplied per account, not in the spec.

**`Tick`** (built each poll from mt5-core): `ts`, `equity`, `balance`, `day_realized_pnl`,
`traded_today`, plus the open-positions list passed through for the read-model.

**`EngineMemory`** (the only thing carried between polls; kept tiny): `day_key`, `day_anchor`,
`day_start_equity`, `peak`, `dd_floor_locked`, `daily_results` (day_key → realized pnl),
`trading_days`, `traded_today_seen`.

### Output — `AccountState` (the one object)

```jsonc
{
  "account_id", "ts", "equity", "balance", "peak", "day_anchor",
  "status": "HEALTHY|CAUTION|WARNING|CRITICAL|LOCKED",
  "buffers": {
    "daily":   { "floor", "room", "ratio", "allowance", "breached" },
    "maxDD":   { "floor", "room", "ratio", "allowance", "breached" },
    "personalDaily": { ... } | null,   // amber line, stricter-only
    "personalDD":    { ... } | null
  },
  "challenge": {
    "profit", "target", "to_go", "days_traded", "days_owed", "passed",
    "consistency": { "biggest_day", "share", "cap", "ceiling", "state" } | null,
    "plan":        { "band_lo", "band_hi", "days_left", "ceil_day", "on_track" } | null
  },
  "violations": [ { "kind", "is_firm", "message" } ],
  "breached": bool
}
```

`room` = money before breach (can go negative). `ratio` = fraction of allowance consumed, 0..1.
Status tiers on ratio: CAUTION ≥ 0.50, WARNING ≥ 0.75, CRITICAL ≥ 0.90, breach → CRITICAL.

### Personal limits (stricter-only clamp)

Personal limits are a fraction (10–100%) of the firm allowance, enforced first (amber trips before
red). **Invariant: a personal floor can never sit outside the firm floor** (`max(personal_floor,
firm_floor)`). This is non-negotiable — it is the personalization that *is* the product.

### Tests (the entire test surface for v1)

Unit tests on buffer math only — no stress-path harness:
- daily floor from each `basis`/`anchor` combination
- static DD floor vs trailing DD floor vs trailing-then-lock latch
- consistency ceiling + share
- pass-plan band + `on_track`
- personal stricter-only clamp never sits below the firm floor

---

## 4. Backend — new `guard` domain

Follows the existing domain layout and RULES.md layering
(`router → service → repository → models`; repos `flush()`, services `commit()` at public entry
points only; authorization in the service via `get_*_for_user`; `Decimal` money; UTC-aware datetimes).

```
src/app/domains/guard/
  __init__.py
  models.py        # GuardAccount, GuardState, GuardDailyResult, GuardTick, GuardAlert
  schemas.py       # Pydantic: rule-spec input, monitor/rules response shapes
  repository.py    # data access (flush only)
  service.py       # enable/disable Guard, rules CRUD, read-model assembly (commits)
  router.py        # /guard endpoints
  engine/          # ported pure engine (core.py, dayclock.py)
  spec.py          # FirmRuleSpec (hydrated from JSON)
  state.py         # AccountState dataclasses
  money.py enums.py tick.py
  views.py         # monitor_view / copilot_view / rules_view projections
  watcher.py       # poll → engine → persist → alert (called by the Celery task)
  alerts.py        # tier-escalation detection + email dispatch (de-dupe)
```

### 4.1 Models (new tables)

All UUID PKs, UTC-aware `created_at`/`updated_at`, registered in `src/app/models/__init__.py`.

**`guard_accounts`** — one row per account with Guard enabled.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `trading_account_id` | UUID FK → `trading_accounts.id` | CASCADE; unique (one Guard config per account) |
| `user_id` | UUID FK → `users.id` | CASCADE, indexed (ownership + list scoping) |
| `enabled` | bool | default True; drives whether the watcher polls |
| `size` | Decimal | challenge starting balance (the spec is %; this anchors it) |
| `rule_spec_json` | JSONB | the user-entered `FirmRuleSpec` |
| `personal_json` | JSONB | `{ daily_frac, dd_frac }` stricter-only clamp; nullable |
| `contract_text` | String | optional custom line prepended to the generated contract |
| `last_polled_at` | datetime tz | nullable |
| `connection_health` | String(32) | `ok` \| `offline`; drives the "monitoring offline" alert |
| `created_at` / `updated_at` | datetime tz | |

**`guard_state`** — latest snapshot, upserted each poll (latest only, not history).

`guard_account_id` (FK, unique), `ts`, `equity`, `balance`, `peak`, `day_anchor`, `status`,
`buffers_json`, `challenge_json`. Money columns `Decimal`.

**`guard_daily_results`** — closed-day P&L (durable; feeds consistency + min-days).

`guard_account_id` (FK), `date`, `pnl` (Decimal), `trade_count`, `is_trading_day`. Unique
`(guard_account_id, date)`.

**`guard_ticks`** — short rolling window for the intraday equity chart (NOT full history).

`guard_account_id` (FK), `ts`, `equity`. Pruned to the last N points per account (e.g. keep ~120).

**`guard_alerts`** — sent log + de-dupe + audit.

`guard_account_id` (FK), `ts`, `tier` (the standing crossed), `kind`, `channel`, `sent_ok`.

> Storage discipline (ship-plan §4): store closed-day results, not every tick forever. Ticks are
> transient — a short rolling window for the chart, pruned continuously.

### 4.2 Watcher (Celery)

A periodic Celery task (named e.g. `guard.poll_account`, `bind=True`), one run per enabled account.

```python
@celery_app.task(name="guard.poll_account", bind=True, max_retries=0)
def poll_account(self, guard_account_id: str) -> dict:
    with SessionLocal() as db:
        guard_acc = guard_repo.get_guard_account(db, uuid.UUID(guard_account_id))
        if guard_acc is None or not guard_acc.enabled:
            return {"status": "skipped"}
        watcher.run_one_poll(db, guard_acc)   # mt5-core → engine → persist → alert
        db.commit()
```

Per poll:
1. Decrypt the linked `TradingAccount` credentials; call mt5-core `get_open_positions` →
   `{equity, balance, floating_pnl, positions}`.
2. Build a `Tick`; load `EngineMemory` (rehydrated from `guard_state` + `guard_daily_results`); run
   `Engine.process`.
3. Upsert `guard_state`; upsert today's `guard_daily_results`; append/prune `guard_ticks`.
4. Run the alert dispatcher (§4.3).
5. On mt5-core failure: set `connection_health = offline`, **email "monitoring offline — close
   manually if needed"** once on transition, backoff, retry. Never fail silently (ship-plan §5).

**Scheduling:** always-on while `enabled`. Fan-out worker that enqueues one `poll_account` per enabled
guard account every N seconds. **Poll interval is the one open knob** — default 5–15s, tuned against
mt5-core latency/cost. Document this as the explicit RULES.md exception (Guard is allowed scheduled
polling; the rest of the app is not).

### 4.3 Alerts (email, de-duped on escalation)

Mirror the prototype's escalation guard exactly: keep the **last standing** per account; fire **only
when the tier escalates** (`HEALTHY → CAUTION → WARNING → CRITICAL → BREACHED`), never on de-escalation
or repeats. Each fire writes a `guard_alerts` row and queues a Resend email via the existing pattern:

```python
@celery_app.task(name="guard.send_alert_email", bind=True, max_retries=3,
                 default_retry_delay=30, autoretry_for=(Exception,))
def send_alert_email_task(self, to_email: str, tier: str, payload: dict) -> None:
    from app.shared.utils.email import send_guard_alert_email   # new helper, _render_email template
    send_guard_alert_email(to_email, tier, payload)
```

Copy carries the honest framing: *"Close to your daily line — $X left. We can only warn, not stop it."*

### 4.4 API surface

`APIRouter(prefix="/guard", tags=["guard"])`, each endpoint `Depends(get_current_user)` +
`Depends(get_db)`, authorization in the service. Registered in `src/app/main.py` via
`app.include_router(guard_router)`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/guard/accounts` | list the user's Guard-enabled accounts + status |
| `POST` | `/guard/accounts` | enable Guard on a connected `TradingAccount` (body: `trading_account_id`, `size`, `rule_spec`, `personal`, `contract_text`) |
| `GET` | `/guard/accounts/{id}` | one Guard account config |
| `PATCH` | `/guard/accounts/{id}` | edit rules / personal limits / enabled |
| `DELETE` | `/guard/accounts/{id}` | disable + remove Guard (account stays connected) |
| `GET` | `/guard/accounts/{id}/monitor` | **the awareness dashboard payload**: latest `AccountState` (distance-to-line headline + pass progress) + open positions + rolling tick window for the chart + recent alerts |
| `GET` | `/guard/accounts/{id}/rules` | entered firm rules + personal clamp + generated plain-English **contract** |

`/monitor` is intentionally one fat read-model so the FE dashboard makes a single call. (Copilot/pass
plan is a panel inside it, not a separate route.)

---

## 5. Frontend — `guard` feature slice + nav rail

Feature-sliced module + a top-level app in `nav-registry.ts`, behind a flag in `feature-flags.ts`
(default off). The `awareness-dashboard.jsx` prototype is the visual target for `/guard`.

```
/guard            Awareness dashboard (the prototype, fed by GET /guard/accounts/{id}/monitor)
                    ├─ Distance to breach   (headline: daily + max-DD meters, $ room, floor, % left)
                    ├─ Pass progress + plan (target bar, trading-day dots, consistency, plan line)
                    ├─ Intraday equity      (area chart with daily-floor + max-DD reference lines)
                    ├─ Alerts               (channel toggles: Email on; Telegram/Push disabled) + feed
                    └─ Open positions       (read-only)
/guard/rules      Firm rules form (daily/maxDD/target + consistency + min-days, Zod-validated)
                    + personal stricter-only sliders + the generated contract (screenshot-and-pin)
/guard/accounts   Pick a connected account → enable Guard → configure
```

**Data flow:** `/guard` polls `GET /guard/accounts/{id}/monitor` via TanStack Query (e.g. every few
seconds, matching the existing dashboard polling pattern). The FE renders the server's `AccountState`
verbatim — **no client-side floor math**. Channel toggles persist to the Guard config; only Email is
wired in v1.

**Reused:** proxy + auth path, TanStack Query, recharts, notifications bell echo, shadcn/ui.

---

## 6. Phasing

| Phase | Ships | Done when |
|---|---|---|
| **G0 — Engine port** | `guard` domain skeleton + ported pure engine + JSON-spec hydration + unit tests | Engine computes correct buffers/pass-plan from `rule_spec_json`; tests green |
| **G1 — Watcher + persistence** | Celery poll loop, `guard_state` / `guard_daily_results` / `guard_ticks`, offline alert | A real connected account produces live snapshots; disconnect emails once |
| **G2 — API + FE rail** | `/guard/*` endpoints + Awareness dashboard + Rules form | A trader enables Guard, enters rules, sees live distance-to-line |
| **G3 — Email alerts** | Resend on tier escalation, de-duped | Crossing CAUTION/WARNING/CRITICAL/breach sends exactly one clean email |

**G2 is the demoable wedge** — Monitor on a live account showing real distance-to-line. Put that in
front of a trader first.

---

## 7. Trust, liability, scope (non-negotiable)

- **Never "you can't blow up."** Always "guardrails that catch the slow bleeds." Read-only Guard
  *reduces* breach risk; it does not guarantee passing or prevent all losses — outages, gaps and news
  spikes are disclaimed. This line appears on the Rules surface contract and in alert copy.
- **Credentials** are already Fernet-encrypted in `TradingAccount`; Guard reuses them, never logs them.
- **Latency honesty:** app → mt5-core → terminal → broker adds hops; fine for intraday/swing. Scalpers
  / news traders are out of scope for v1 and we say so.
- **Prop-firm ToS:** frame Guard as *discipline and rule-adherence*, never "we game the evaluation."
  (Allowlist/per-firm ToS handling is deferred with the firm library — not needed while the trader
  enters their own rules and Guard only *watches*.)

---

## 8. Open knobs (decide during build, not blockers)

1. **Poll interval** — default 5–15s; tune against mt5-core latency/cost.
2. **Tick-window size** — points kept per account for the chart (≈120).
3. **Contract copy** — final wording of the plain-English Rules-surface statement.

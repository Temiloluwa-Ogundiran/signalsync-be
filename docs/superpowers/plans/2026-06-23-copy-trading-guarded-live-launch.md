# Copy Trading Guarded Live Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Telegram-to-MT5 copying deterministic, auditable, fail-safe, and operationally ready for a guarded live-money launch.

**Architecture:** Preserve the current Redis-stream pipeline and durable copy-trading models, but add explicit opening generations inside each route assembly, separate persistent opening context from later management actions, and drive terminal state from broker confirmation. Confidence becomes advisory while deterministic validation controls execution. Production rollout remains paused until migration, service readiness, synthetic broker acceptance, alerting, and rollback checks pass.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL/Alembic, Redis Streams, Telethon, OpenAI structured output, Python MetaTrader5, Next.js 16, React Query, TypeScript, pytest, Node test runner, Railway CLI.

---

## File Structure

### Backend

- Modify `src/app/domains/copy_trading/models.py`: generation state and terminal metadata.
- Modify `src/app/domains/copy_trading/assembly.py`: deterministic conversation selection and context merging.
- Create `src/app/domains/copy_trading/generations.py`: pure opening-generation transition functions.
- Modify `src/app/domains/copy_trading/engine.py`: structural validation with advisory confidence.
- Modify `src/app/domains/copy_trading/workers.py`: signal ingestion, intent creation, broker completion, and reconciliation transitions.
- Modify `src/app/domains/copy_trading/health.py`: launch readiness and unresolved-risk reporting.
- Modify `src/app/domains/copy_trading/router.py`: launch-readiness API and operational controls.
- Modify `src/app/domains/copy_trading/schemas.py`: generation and readiness response contracts.
- Create `alembic/versions/d0e1f2a3b4c5_add_copy_execution_generations.py`: additive state migration.
- Modify `docs/copy-trading-operations.md`: guarded launch, alerts, rollback, and incident handling.

### MT5

- Modify `app/mt5_worker/idempotency.py`: exact client identifier lookup.
- Modify `app/mt5_worker/handlers/orders.py`: preflight, duplicate replay, and submission-start evidence.
- Modify `app/mt5_worker/handlers/positions.py`: exact management-action outcomes.
- Modify `app/mt5_worker/handlers/accounts.py`: reconciliation snapshot and trading permission.

### Frontend

- Modify `src/features/copy-trading/types.ts`: generation and launch-readiness types.
- Modify `src/features/copy-trading/api.ts`: readiness and generation activity contracts.
- Modify `src/features/copy-trading/copy-trading-view-model.ts`: trader-facing lifecycle wording.
- Modify `src/features/copy-trading/activity/activity-item.tsx`: waiting, submitting, confirming, and terminal states.
- Modify `src/features/copy-trading/health/copy-system-status.tsx`: launch blockers and operational health.
- Modify `src/features/copy-trading/overview/attention-list.tsx`: actionable unresolved-risk notices.
- Modify `src/features/copy-trading/setup/preferences-step.tsx`: exact assembly-window and unsafe-entry wording.
- Modify `src/features/copy-trading/settings/copy-trading-settings-page.tsx`: remove remaining learning terminology.

## Task 1: Add Explicit Opening Generations

**Files:**
- Modify: `src/app/domains/copy_trading/models.py`
- Modify: `src/app/domains/copy_trading/schemas.py`
- Create: `alembic/versions/d0e1f2a3b4c5_add_copy_execution_generations.py`
- Test: `tests/test_copy_trading_models.py`
- Test: `tests/test_copy_trading_reliability_migration.py`

- [ ] **Step 1: Write failing model tests**

Add assertions that a route assembly stores its opening generation, opening action, submission state, and terminal reason.

```python
def test_route_assembly_tracks_opening_generation():
    columns = RouteSignalAssembly.__table__.columns
    assert "generation" in columns
    assert "opening_action" in columns
    assert "opening_intent_id" in columns
    assert "terminal_reason" in columns
    assert "completed_at" in columns


def test_route_assembly_generation_is_positive():
    checks = {constraint.name for constraint in RouteSignalAssembly.__table__.constraints}
    assert "ck_route_signal_assembly_generation_positive" in checks
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_copy_trading_models.py tests/test_copy_trading_reliability_migration.py -q
```

Expected: failure because the generation columns and migration do not exist.

- [ ] **Step 3: Add the model fields**

Add to `RouteSignalAssembly`:

```python
generation: Mapped[int] = mapped_column(nullable=False, default=1)
opening_action: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
opening_intent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("trade_intents.id", ondelete="SET NULL"),
    nullable=True,
)
terminal_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
completed_at: Mapped[Optional[datetime]] = mapped_column(
    DateTime(timezone=True),
    nullable=True,
)
```

Add `CheckConstraint("generation > 0", name="ck_route_signal_assembly_generation_positive")`.

- [ ] **Step 4: Add the additive migration**

Create revision `d0e1f2a3b4c5` with `down_revision = "c9d8e7f6a5b4"`. Add nullable/backfilled columns without deleting historical rows. Backfill `generation=1`. Existing `executing` assemblies remain recoverable and are not marked completed by migration.

- [ ] **Step 5: Run model and migration tests**

Run:

```powershell
uv run pytest tests/test_copy_trading_models.py tests/test_copy_trading_reliability_migration.py tests/test_alembic_revision_compatibility.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/app/domains/copy_trading/models.py src/app/domains/copy_trading/schemas.py alembic/versions/d0e1f2a3b4c5_add_copy_execution_generations.py tests
git commit -m "feat(copy-trading): track opening generations"
```

## Task 2: Preserve Opening Intent Across Split Messages

**Files:**
- Create: `src/app/domains/copy_trading/generations.py`
- Modify: `src/app/domains/copy_trading/assembly.py`
- Modify: `src/app/domains/copy_trading/workers.py`
- Test: `tests/test_copy_trading_assembly.py`
- Test: `tests/test_copy_trading_workers.py`

- [ ] **Step 1: Write failing split-signal tests**

```python
def test_later_sl_enriches_incomplete_open_instead_of_replacing_action():
    context = {
        "action": "open_market",
        "direction": "buy",
        "symbol": "XAUUSD",
    }
    update = {
        "action": "modify_sl_tp",
        "stop_loss": "2315",
    }

    merged = merge_generation_context(context, update, opening_submitted=False)

    assert merged["action"] == "open_market"
    assert merged["stop_loss"] == "2315"


def test_later_sl_becomes_management_action_after_open_confirmation():
    merged = merge_generation_context(
        {"action": "open_market", "direction": "buy", "symbol": "XAUUSD"},
        {"action": "modify_sl_tp", "stop_loss": "2315"},
        opening_submitted=True,
    )

    assert merged["action"] == "modify_sl_tp"
```

Add an integration-style worker test:

```python
def test_buy_then_sl_creates_one_open_intent(signal_pipeline):
    signal_pipeline.publish("BUY XAUUSD")
    signal_pipeline.publish("SL 2315")

    intents = signal_pipeline.trade_intents()

    assert len(intents) == 1
    assert intents[0].request_payload["action"] == "open_market"
    assert intents[0].request_payload["stop_loss"] == "2315"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_copy_trading_assembly.py tests/test_copy_trading_workers.py -q
```

Expected: the action is overwritten by `modify_sl_tp`, or no opening intent is created.

- [ ] **Step 3: Implement pure generation merging**

Create:

```python
OPEN_ACTIONS = {"open_market", "place_pending"}
MANAGEMENT_ACTIONS = {
    "modify_sl_tp",
    "break_even",
    "partial_close",
    "full_close",
    "cancel_pending",
    "additional_tp",
}


def merge_generation_context(
    current: dict,
    update: dict,
    *,
    opening_submitted: bool,
) -> dict:
    merged = merge_context(current, update)
    current_action = current.get("action")
    update_action = update.get("action")
    if (
        not opening_submitted
        and current_action in OPEN_ACTIONS
        and update_action in MANAGEMENT_ACTIONS
    ):
        merged["action"] = current_action
    return merged
```

Do not copy `None`, empty strings, or empty TP lists over known values unless an edited Telegram message explicitly removes a field.

- [ ] **Step 4: Use generation merging in `signal_handler`**

Determine `opening_submitted` from `assembly.opening_intent_id`. Preserve the opening action until the first opening intent exists. Store the latest parsed message separately in `ParsedAction.payload`; store the persistent opening context in `RouteSignalAssembly.context`.

- [ ] **Step 5: Pass focused tests**

Run:

```powershell
uv run pytest tests/test_copy_trading_assembly.py tests/test_copy_trading_workers.py tests/test_copy_trading_signal_parser.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/app/domains/copy_trading/generations.py src/app/domains/copy_trading/assembly.py src/app/domains/copy_trading/workers.py tests
git commit -m "fix(copy-trading): preserve split opening signals"
```

## Task 3: Make Confidence Advisory And Validation Deterministic

**Files:**
- Modify: `src/app/domains/copy_trading/engine.py`
- Modify: `src/app/domains/copy_trading/workers.py`
- Modify: `src/app/domains/copy_trading/schemas.py`
- Test: `tests/test_copy_trading_signal_parser.py`
- Test: `tests/test_copy_trading_workers.py`
- Test: `tests/test_copy_trading_schemas.py`

- [ ] **Step 1: Write failing confidence tests**

```python
def test_low_confidence_complete_signal_is_accepted():
    signal = ParsedSignal(
        action=SignalAction.open_market,
        symbol="XAUUSD",
        direction="buy",
        stop_loss=Decimal("2315"),
        take_profits=[Decimal("2340")],
        confidence=0.42,
    )

    result = validate_signal(signal, RouteExecutionPolicy())

    assert result.accepted is True
    assert result.advisory == "AI confidence is low."
```

Add tests that schema-invalid output, ambiguity, missing required fields, stale market entries, disabled actions, and unresolved symbols still block execution.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_copy_trading_signal_parser.py tests/test_copy_trading_workers.py tests/test_copy_trading_schemas.py -q
```

Expected: low confidence is rejected.

- [ ] **Step 3: Extend `ValidationResult`**

```python
@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason: Optional[str] = None
    advisory: Optional[str] = None
```

Remove confidence as a rejection condition. Set an advisory when confidence is below the configured threshold.

- [ ] **Step 4: Record the advisory**

Persist confidence, advisory, parser model, parser version, and parse latency in `ParsedAction.validation_result` and activity details. The title remains `Signal ready`; the UI may display advisory context without changing route eligibility.

- [ ] **Step 5: Pass focused tests**

Run:

```powershell
uv run pytest tests/test_copy_trading_signal_parser.py tests/test_copy_trading_workers.py tests/test_copy_trading_schemas.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/app/domains/copy_trading/engine.py src/app/domains/copy_trading/workers.py src/app/domains/copy_trading/schemas.py tests
git commit -m "fix(copy-trading): make AI confidence advisory"
```

## Task 4: Terminate Every Generation From Broker Truth

**Files:**
- Modify: `src/app/domains/copy_trading/workers.py`
- Modify: `src/app/domains/copy_trading/reconciliation.py`
- Modify: `src/app/domains/copy_trading/generations.py`
- Test: `tests/test_copy_trading_workers.py`
- Test: `tests/test_copy_trading_reconciliation.py`
- Test: `tests/test_copy_trading_end_to_end.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_confirmed_open_completes_generation(db, confirmed_intent):
    complete_intent_generation(db, confirmed_intent)
    assembly = db.get(RouteSignalAssembly, confirmed_intent.assembly_id)
    assert assembly.state == RouteAssemblyState.completed
    assert assembly.completed_at is not None


def test_permanent_broker_failure_terminates_generation(db, failed_intent):
    fail_intent_generation(db, failed_intent, "INVALID_VOLUME")
    assembly = db.get(RouteSignalAssembly, failed_intent.assembly_id)
    assert assembly.state == RouteAssemblyState.failed
    assert assembly.terminal_reason == "INVALID_VOLUME"
```

Add expiry, skipped, superseded edit, uncertain, reconciliation-confirmed, and reconciliation-failed cases.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_copy_trading_workers.py tests/test_copy_trading_reconciliation.py tests/test_copy_trading_end_to_end.py -q
```

Expected: assemblies remain in `executing`.

- [ ] **Step 3: Add generation transition helpers**

Implement:

```python
def mark_generation_submitted(assembly, intent_id, now): ...
def mark_generation_completed(assembly, now): ...
def mark_generation_failed(assembly, reason, now): ...
def mark_generation_expired(assembly, reason, now): ...
def mark_generation_superseded(assembly, reason, now): ...
```

Each helper validates the source state and sets terminal metadata atomically.

- [ ] **Step 4: Wire broker outcomes**

On successful opening or pending-order submission, mark the assembly completed. On permanent failure, mark it failed. On uncertain outcome, leave it executing while reconciliation is active. When reconciliation confirms or permanently resolves the intent, transition the assembly.

Management actions create their own `TradeIntent` but do not reopen generation 1.

- [ ] **Step 5: Ensure conversations remain manageable**

Set the conversation opening status to complete after generation 1. Keep the conversation active while copied trades are open or pending. Expire it after all linked copied trades are terminal and the configured quiet period passes.

- [ ] **Step 6: Pass focused and end-to-end tests**

Run:

```powershell
uv run pytest tests/test_copy_trading_workers.py tests/test_copy_trading_reconciliation.py tests/test_copy_trading_end_to_end.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add src/app/domains/copy_trading/generations.py src/app/domains/copy_trading/workers.py src/app/domains/copy_trading/reconciliation.py tests
git commit -m "fix(copy-trading): complete broker generations"
```

## Task 5: Harden Edits, Additional TPs, And Management Matching

**Files:**
- Modify: `src/app/domains/copy_trading/assembly.py`
- Modify: `src/app/domains/copy_trading/workers.py`
- Modify: `src/app/domains/copy_trading/reconciliation.py`
- Test: `tests/test_copy_trading_assembly.py`
- Test: `tests/test_copy_trading_workers.py`
- Test: `tests/test_copy_trading_end_to_end.py`

- [ ] **Step 1: Write failing behavior tests**

Cover:

```python
def test_edit_before_submission_supersedes_unsubmitted_generation(): ...
def test_edit_after_submission_creates_corrective_management_intent(): ...
def test_additional_tp_opens_one_extra_leg_when_enabled(): ...
def test_additional_tp_is_skipped_when_disabled(): ...
def test_symbol_less_management_update_uses_only_unambiguous_recent_trade(): ...
def test_management_update_does_not_target_closed_trade(): ...
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
uv run pytest tests/test_copy_trading_assembly.py tests/test_copy_trading_workers.py tests/test_copy_trading_end_to_end.py -q
```

- [ ] **Step 3: Implement edit semantics**

Before submission, update the existing generation and increment its revision. After submission, compare material fields and create only the required SL/TP or cancellation action. Never create a second ordinary opening from an edit.

- [ ] **Step 4: Implement copied-trade targeting**

Matching priority:

1. Exact conversation.
2. Exact broker-confirmed symbol.
3. Most recent broker-confirmed matching trade.

If more than one candidate remains, record `signal.ambiguous` and take no broker action.

- [ ] **Step 5: Pass tests and commit**

```powershell
uv run pytest tests/test_copy_trading_assembly.py tests/test_copy_trading_workers.py tests/test_copy_trading_end_to_end.py -q
git add src/app/domains/copy_trading tests
git commit -m "fix(copy-trading): harden signal updates"
```

## Task 6: Strengthen MT5 Preflight And Exact-Once Evidence

**Files:**
- Modify: `app/mt5_worker/idempotency.py`
- Modify: `app/mt5_worker/handlers/orders.py`
- Modify: `app/mt5_worker/handlers/positions.py`
- Modify: `app/mt5_worker/handlers/accounts.py`
- Test: `app/tests/test_copy_trading_idempotency.py`
- Test: `app/tests/test_copy_trading_execution.py`
- Test: `app/tests/test_worker_orders.py`
- Test: `app/tests/test_worker_positions.py`

- [ ] **Step 1: Write failing MT5 tests**

```python
def test_timeout_after_acceptance_reports_submission_started(fake_mt5):
    fake_mt5.accept_then_timeout()
    result = execute_order_job(order_job("client-123"))
    assert result["submission_started"] is True


def test_duplicate_client_id_returns_existing_broker_object(fake_mt5):
    first = execute_order_job(order_job("client-123"))
    second = execute_order_job(order_job("client-123"))
    assert first["order"]["order"] == second["order"]["order"]
    assert fake_mt5.order_send.call_count == 1
```

Also test market closure, trade-disabled account, invalid symbol, min/max/step volume, insufficient margin, pending-order rejection, exact position modification, and partial-close volume.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest app/tests/test_copy_trading_idempotency.py app/tests/test_copy_trading_execution.py app/tests/test_worker_orders.py app/tests/test_worker_positions.py -q
```

- [ ] **Step 3: Add explicit preflight result**

Before `order_send`, return structured rejection codes from `order_check`, symbol metadata, account trading permission, and volume normalization. Include `submission_started` in every result or exception payload.

- [ ] **Step 4: Preserve exact idempotency**

Search live positions, pending orders, history orders, and deals using the stable compact client identifier before submission. Return the existing broker object when found.

- [ ] **Step 5: Run the full MT5 suite**

```powershell
pytest -q
```

- [ ] **Step 6: Commit**

```powershell
git add app/mt5_worker app/tests
git commit -m "fix(mt5): harden copy execution preflight"
```

## Task 7: Add Launch Readiness And Alerts

**Files:**
- Modify: `src/app/domains/copy_trading/health.py`
- Modify: `src/app/domains/copy_trading/router.py`
- Modify: `src/app/domains/copy_trading/schemas.py`
- Modify: `src/app/domains/copy_trading/worker_runtime.py`
- Modify: `src/app/core/config.py`
- Modify: `.env.example`
- Test: `tests/test_copy_trading_health.py`
- Test: `tests/test_copy_trading_router.py`
- Test: `tests/test_copy_trading_runtime.py`

- [ ] **Step 1: Write failing launch-readiness tests**

```python
def test_launch_readiness_blocks_old_uncertain_intent():
    readiness = build_launch_readiness(
        components=healthy_components(),
        uncertain_intent_ages=[121],
        dead_letter_count=0,
    )
    assert readiness.ready is False
    assert "uncertain_intents" in readiness.blockers


def test_launch_readiness_requires_global_kill_switch():
    readiness = build_launch_readiness(
        components=healthy_components(),
        kill_switch_available=False,
    )
    assert readiness.ready is False
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
uv run pytest tests/test_copy_trading_health.py tests/test_copy_trading_router.py tests/test_copy_trading_runtime.py -q
```

- [ ] **Step 3: Add readiness contract**

Expose `GET /copy-trading/launch-readiness` with:

```json
{
  "ready": false,
  "blockers": ["uncertain_intents"],
  "warnings": [],
  "components": [],
  "stream_lag": 0,
  "pending_events": 0,
  "dead_letters": 0,
  "oldest_uncertain_seconds": 121
}
```

Readiness requires fresh Telegram, signal, execution, Redis, PostgreSQL, parser, MT5 API, and MT5 worker evidence.

- [ ] **Step 4: Add alert thresholds**

Add environment-backed settings for stale heartbeat, parser failure rate, uncertain intent age, stream lag, pending count, and dead-letter count. Emit structured error logs and Sentry events without secrets.

- [ ] **Step 5: Pass tests and commit**

```powershell
uv run pytest tests/test_copy_trading_health.py tests/test_copy_trading_router.py tests/test_copy_trading_runtime.py -q
git add src/app/domains/copy_trading src/app/core/config.py .env.example tests
git commit -m "feat(copy-trading): add launch readiness gates"
```

## Task 8: Make The Frontend Show The True Lifecycle

**Files:**
- Modify: `src/features/copy-trading/types.ts`
- Modify: `src/features/copy-trading/api.ts`
- Modify: `src/features/copy-trading/copy-trading-view-model.ts`
- Modify: `src/features/copy-trading/activity/activity-item.tsx`
- Modify: `src/features/copy-trading/health/copy-system-status.tsx`
- Modify: `src/features/copy-trading/overview/attention-list.tsx`
- Modify: `src/features/copy-trading/setup/preferences-step.tsx`
- Modify: `src/features/copy-trading/settings/copy-trading-settings-page.tsx`
- Test: `src/features/copy-trading/copy-trading-contract.test.ts`
- Test: `src/features/copy-trading/copy-trading-view-model.test.ts`

- [ ] **Step 1: Write failing frontend tests**

Assert:

```typescript
test("copy trading contains no historical learning workflow", () => {
  assert.doesNotMatch(featureSource, /learn|analyze again|unsupported channel/i);
});

test("activity distinguishes waiting from broker confirmation", () => {
  assert.equal(activityLabel("signal.waiting"), "Waiting for trade details");
  assert.equal(activityLabel("broker.uncertain"), "Confirming with broker");
});

test("launch blockers are never presented as ready", () => {
  assert.equal(
    launchStatus({ ready: false, blockers: ["uncertain_intents"] }).tone,
    "danger",
  );
});
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
npx tsx --test src/features/copy-trading/copy-trading-contract.test.ts src/features/copy-trading/copy-trading-view-model.test.ts
```

- [ ] **Step 3: Implement the lifecycle UI**

Display:

- Waiting for trade details
- Signal expired
- Signal needs a clear reference
- Ready to submit
- Sending to broker
- Confirming with broker
- Trade completed
- Trade skipped
- Trade failed

Keep low confidence as advisory text only. Remove all remaining learning/support wording and API usage.

- [ ] **Step 4: Add launch blocker UI**

The system-status panel must list each blocker with a corrective action. Global pause remains available even when other API calls fail.

- [ ] **Step 5: Run frontend verification**

```powershell
npx tsx --test src/features/copy-trading/copy-trading-contract.test.ts src/features/copy-trading/copy-trading-view-model.test.ts
npm run lint
npm run build
```

Expected: tests and build pass; lint has zero errors.

- [ ] **Step 6: Commit**

```powershell
git add src/features/copy-trading
git commit -m "fix(copy-trading): show launch-safe lifecycle"
```

## Task 9: Build A Deterministic Launch Acceptance Harness

**Files:**
- Modify: `tests/test_copy_trading_end_to_end.py`
- Create: `tests/fakes/fake_copy_parser.py`
- Modify: `tests/fakes/fake_mt5_broker.py`
- Modify: `tests/conftest.py`
- Create: `scripts/copy_trading_launch_check.py`

- [ ] **Step 1: Write failing end-to-end scenarios**

The harness must prove:

```python
def test_split_signal_opens_once_with_later_sl(copy_pipeline): ...
def test_timeout_after_acceptance_reconciles_without_duplicate(copy_pipeline): ...
def test_restart_recovers_unresolved_intent(copy_pipeline): ...
def test_two_symbols_never_share_context(copy_pipeline): ...
def test_ambiguous_symbol_less_update_moves_no_money(copy_pipeline): ...
def test_additional_tp_respects_route_policy(copy_pipeline): ...
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
uv run pytest tests/test_copy_trading_end_to_end.py -q
```

- [ ] **Step 3: Complete deterministic fakes**

The fake parser returns predefined structured actions by Telegram message ID. The fake broker records client IDs, supports accept-then-timeout, and exposes reconciliation snapshots.

- [ ] **Step 4: Add the launch checker**

`scripts/copy_trading_launch_check.py` must:

1. Query launch readiness.
2. Verify migrations at head.
3. Verify all required worker roles.
4. Verify no pending dead letters.
5. Verify no old uncertain intents.
6. Publish or invoke the configured synthetic test flow.
7. Confirm one correlation ID and one client order ID across activity, intent, broker, and copied trade.
8. Exit nonzero on any blocker.

- [ ] **Step 5: Run the complete backend suite**

```powershell
uv run pytest -q --ignore=tests/test_journal_repository_db.py
```

- [ ] **Step 6: Commit**

```powershell
git add tests scripts/copy_trading_launch_check.py
git commit -m "test(copy-trading): add guarded launch acceptance"
```

## Task 10: Remove Obsolete Learning Runtime And Documentation

**Files:**
- Modify: `src/app/domains/copy_trading/models.py`
- Modify: `src/app/domains/copy_trading/schemas.py`
- Modify: `src/app/domains/copy_trading/worker_runtime.py`
- Modify: `src/app/domains/copy_trading/telegram_auth.py`
- Modify: `src/app/domains/copy_trading/health.py`
- Modify: `docs/copy-trading-operations.md`
- Test: `tests/test_copy_trading_runtime.py`
- Test: `tests/test_copy_trading_telegram.py`
- Test: `tests/test_copy_trading_worker_startup.py`

- [ ] **Step 1: Write failing absence tests**

Assert there is no `source.learn` command, learning worker role, support classifier, image-primary source pause, or learning health dependency.

- [ ] **Step 2: Run tests and verify RED**

```powershell
uv run pytest tests/test_copy_trading_runtime.py tests/test_copy_trading_telegram.py tests/test_copy_trading_worker_startup.py -q
```

- [ ] **Step 3: Remove executable learning code**

Delete learning handlers, prompts, recovery jobs, and worker role routing. Preserve old profile tables only for backward-compatible audit reads until a later destructive migration. Image-only messages create an activity warning and return success.

- [ ] **Step 4: Update operations documentation**

Required workers become:

- `telegram-session`
- `copy-signal`
- `copy-execution`

Replace learning procedures with runtime assembly, parser failure, and signal-expiry procedures.

- [ ] **Step 5: Pass tests and commit**

```powershell
uv run pytest tests/test_copy_trading_runtime.py tests/test_copy_trading_telegram.py tests/test_copy_trading_worker_startup.py -q
git add src/app/domains/copy_trading docs/copy-trading-operations.md tests
git commit -m "refactor(copy-trading): remove learning runtime"
```

## Task 11: Create And Configure Railway Production

**Files:**
- Modify if needed: `.env.example`
- Modify: `docs/copy-trading-operations.md`

- [ ] **Step 1: Inventory dev services and shared variables**

Run:

```powershell
railway environment list --json
railway service list -e dev --json
railway variables -e dev --json
```

Record service names without printing secret values in logs or documentation.

- [ ] **Step 2: Create production by duplicating dev configuration**

Run:

```powershell
railway environment new production --duplicate dev --json
```

If production already exists, inspect it and reconcile service differences rather than recreating it.

- [ ] **Step 3: Disable automation before deployment**

Set production shared variables:

```text
COPY_TRADING_ENABLED=false
COPY_TRADING_GLOBAL_PAUSED=true
IS_PRODUCTION=true
DEBUG=false
```

Generate production-only secrets and domains. Do not reuse development database credentials, encryption keys, Telegram session data, or test broker credentials.

- [ ] **Step 4: Remove obsolete service**

Delete `copy-learning-worker` from production and ensure no health check references it.

- [ ] **Step 5: Configure required services**

Required production services:

- PostgreSQL/HA and PgBouncer
- Redis
- `tradepartna-api`
- `tradepartna-worker`
- `telegram-session-worker`
- `copy-signal-worker`
- `copy-execution-worker`
- `mt5-api`
- `mt5-worker`
- `tradepartna-fe`

Use shared variables for database, Redis, encryption, email, OpenAI, Telegram, MT5 internal authentication, and public URLs. Use service-specific process commands only for worker role selection.

- [ ] **Step 6: Document actual production service IDs and rollback commands**

Do not document secret values.

## Task 12: Verify, Push, Deploy, And Exercise The Kill Switch

**Files:**
- Modify: `docs/copy-trading-operations.md`

- [ ] **Step 1: Run fresh repository verification**

Backend:

```powershell
uv run pytest -q --ignore=tests/test_journal_repository_db.py
git diff --check
git status --short
```

Frontend:

```powershell
npx tsx --test src/features/copy-trading/copy-trading-contract.test.ts src/features/copy-trading/copy-trading-view-model.test.ts
npm run lint
npm run build
git diff --check
git status --short
```

MT5:

```powershell
pytest -q
git diff --check
git status --short
```

- [ ] **Step 2: Push all three repositories**

Push backend `staging`, frontend `staging`, and MT5 `main` only after their repository verification passes.

- [ ] **Step 3: Deploy in dependency order**

1. Database migration/API.
2. MT5 API and worker.
3. Telegram session worker.
4. Signal worker.
5. Execution worker.
6. General worker.
7. Frontend.

- [ ] **Step 4: Verify migrations and service health**

Run:

```powershell
railway ssh -s tradepartna-api -e production uv run alembic current
railway service list -e production --json
```

Expected migration head: `d0e1f2a3b4c5`.

- [ ] **Step 5: Run production smoke checks while paused**

Verify:

- API and frontend HTTP 200.
- readiness endpoint reports paused rather than ready.
- all worker heartbeats are fresh.
- no dead letters.
- no old uncertain intents.
- MT5 permission check succeeds for the dedicated test account.

- [ ] **Step 6: Run synthetic acceptance**

Execute:

```powershell
railway ssh -s tradepartna-api -e production uv run python scripts/copy_trading_launch_check.py
```

Expected: exit 0 and one exact client ID through the full test flow.

- [ ] **Step 7: Exercise pause and rollback**

1. Resume only the dedicated test route.
2. Confirm one test action.
3. Activate the global pause.
4. Verify a new signal creates no broker intent.
5. Resume.
6. Verify processing returns.

- [ ] **Step 8: Enable guarded live access**

Set `COPY_TRADING_ENABLED=true` only after all acceptance evidence passes. Keep conservative account maximums and global pause available.

- [ ] **Step 9: Record evidence**

Add deployment IDs, migration head, service health timestamps, acceptance correlation ID, test client order ID, pause/resume result, and rollback owner to `docs/copy-trading-operations.md`.

- [ ] **Step 10: Commit and push the operations evidence**

```powershell
git add docs/copy-trading-operations.md
git commit -m "docs(copy-trading): record guarded launch evidence"
git push
```

## Plan Self-Review

- The plan preserves current working Redis, Telegram, MT5, and frontend architecture.
- Split messages cannot replace an incomplete opening action.
- Confidence is advisory while structural and broker safety remain mandatory.
- Every opening generation reaches a terminal or explicitly uncertain state.
- Broker timeout handling remains exact-once from the user's perspective.
- Historical learning is removed from runtime, UI, health, and operations.
- Production starts paused and cannot become live before synthetic acceptance.
- Every production-code task begins with a failing test.
- No destructive database migration is required for launch.
- Legal authorization remains a documented non-code dependency and is not falsely represented as an engineering result.

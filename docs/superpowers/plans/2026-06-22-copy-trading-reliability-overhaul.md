# Copy Trading Reliability Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 22 audited Copy Trading defects and prove correct Telegram-to-MT5 execution without signal mixing, message loss, duplicate broker orders, stale lifecycle state, or misleading UI health.

**Architecture:** Preserve the existing API and data while adding per-route signal assemblies, explicit stream outcomes, exact broker correlation, continuous reconciliation, execution readiness, bounded transport, component health, and independent frontend queries. Extract focused modules from the existing workers so correctness can be tested without running an entire process.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL/Alembic, Redis Streams, Telethon, OpenAI structured output, Python MT5, Next.js 16, React Query, TypeScript, pytest, and Node contract tests.

---

## File Structure

**Backend additions**

- `src/app/domains/copy_trading/delivery.py`: explicit stream outcomes, retries, dead letters, retention, and replay.
- `src/app/domains/copy_trading/assembly.py`: conversation matching and isolated per-route assemblies.
- `src/app/domains/copy_trading/execution.py`: intent creation, exposure checks, account locking, and broker submission.
- `src/app/domains/copy_trading/reconciliation.py`: exact-intent and continuous broker reconciliation.
- `src/app/domains/copy_trading/health.py`: worker heartbeat and aggregate readiness.
- `alembic/versions/a0b1c2d3e4f5_harden_copy_trading_runtime.py`: additive production migration.

**MT5 additions**

- `app/mt5_worker/idempotency.py`: stable client-order comments and exact lookup.
- `tests/test_copy_trading_idempotency.py`: duplicate and timeout behavior.

**Frontend additions**

- `src/features/copy-trading/health/copy-system-status.tsx`: truthful readiness summary.
- `src/features/copy-trading/activity/activity-filters-sheet.tsx`: responsive server filters.
- `src/features/copy-trading/shared/confirm-action-dialog.tsx`: destructive confirmation.

### Task 1: Add Durable Runtime Models And Migration

**Files:**
- Modify: `src/app/domains/copy_trading/models.py`
- Modify: `src/app/domains/copy_trading/schemas.py`
- Create: `alembic/versions/a0b1c2d3e4f5_harden_copy_trading_runtime.py`
- Test: `tests/test_copy_trading_reliability_migration.py`
- Test: `tests/test_copy_trading_models.py`

- [ ] **Step 1: Write failing migration and model tests**

Assert the new schema contains `signal_conversations`, `route_signal_assemblies`, `telegram_auth_attempts`, `copy_dead_letters`, and `copy_worker_health`; assert `trade_intents.client_order_id` is unique; assert copied trades store `original_volume`, `current_volume`, `stop_loss`, `take_profit`, and `broker_synced_at`.

```python
def test_trade_intent_client_order_id_is_unique():
    constraints = {c.name for c in TradeIntent.__table__.constraints}
    assert "uq_trade_intent_client_order_id" in constraints

def test_route_assembly_is_unique_while_active():
    indexes = {i.name for i in RouteSignalAssembly.__table__.indexes}
    assert "uq_route_signal_active_conversation" in indexes
```

- [ ] **Step 2: Run tests and confirm they fail because the models do not exist**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_reliability_migration.py tests/test_copy_trading_models.py -q`

- [ ] **Step 3: Implement additive models and migration**

Use explicit enums for assembly, auth, dead-letter, and health states. Preserve old `signal_threads` for audit. Add a partial unique PostgreSQL index for active route assemblies and non-destructive defaults for existing records.

```python
Index(
    "uq_route_signal_active_conversation",
    "route_id",
    "conversation_id",
    unique=True,
    postgresql_where=text("state IN ('assembling','ready','executing')"),
)
```

- [ ] **Step 4: Run migration/model tests and Alembic head validation**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_reliability_migration.py tests/test_copy_trading_models.py tests/test_alembic_revision_compatibility.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading/models.py src/app/domains/copy_trading/schemas.py alembic/versions/a0b1c2d3e4f5_harden_copy_trading_runtime.py tests
git commit -m "feat(copy-trading): add durable runtime state"
```

### Task 2: Make Redis Delivery Explicit, Retryable, Bounded, And Replayable

**Files:**
- Create: `src/app/domains/copy_trading/delivery.py`
- Modify: `src/app/domains/copy_trading/streams.py`
- Modify: `src/app/domains/copy_trading/worker_runtime.py`
- Modify: `src/app/domains/copy_trading/router.py`
- Test: `tests/test_copy_trading_delivery.py`
- Test: `tests/test_copy_trading_runtime.py`

- [ ] **Step 1: Write failing tests for success, retry, dead letter, replay, and MAXLEN**

```python
def test_transient_handler_result_is_not_acknowledged(worker, redis):
    worker.handler = lambda *_: DeliveryResult.retry("OPENAI_TIMEOUT")
    worker._process_message("1-0", EVENT)
    redis.xack.assert_not_called()

def test_publish_bounds_stream(redis):
    RedisStreamBus(redis).publish(event)
    assert redis.xadd.call_args.kwargs == {"maxlen": 10_000, "approximate": True}
```

- [ ] **Step 2: Run tests and verify the current unconditional acknowledgement fails them**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_delivery.py tests/test_copy_trading_runtime.py -q`

- [ ] **Step 3: Implement explicit outcomes and PostgreSQL dead letters**

Handlers return `DeliveryResult.success()`, `retry(code, delay)`, or `dead_letter(code)`. Retry metadata is stored in event fields. Claiming uses `XAUTOCLAIM`; exceeding attempts persists a dead letter before acknowledgement. Add authenticated list/replay endpoints.

- [ ] **Step 4: Pass delivery tests and verify no handler silently returns on transient failure**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_delivery.py tests/test_copy_trading_runtime.py tests/test_copy_trading_router.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading tests/test_copy_trading_delivery.py tests/test_copy_trading_runtime.py
git commit -m "feat(copy-trading): add durable stream delivery"
```

### Task 3: Isolate Conversations And Route Assemblies

**Files:**
- Create: `src/app/domains/copy_trading/assembly.py`
- Modify: `src/app/domains/copy_trading/workers.py`
- Modify: `src/app/domains/copy_trading/engine.py`
- Test: `tests/test_copy_trading_assembly.py`
- Test: `tests/test_copy_trading_workers.py`

- [ ] **Step 1: Write failing tests for simultaneous symbols, replies, edits, ambiguity, and different route windows**

```python
def test_two_symbols_in_same_channel_create_distinct_conversations():
    eur = assembler.ingest(message(symbol="EURUSD", direction="buy"), routes)
    gold = assembler.ingest(message(symbol="XAUUSD", direction="sell"), routes)
    assert eur.conversation_id != gold.conversation_id

def test_each_route_uses_its_own_deadline():
    assemblies = assembler.ingest(message(symbol="EURUSD"), [route(30), route(90)])
    assert assemblies[1].deadline - assemblies[0].deadline == timedelta(seconds=60)
```

- [ ] **Step 2: Run and observe failures against the shared latest-thread implementation**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_assembly.py -q`

- [ ] **Step 3: Implement locked conversation matching and per-route state**

Use reply root first, then exact symbol/direction, then one unambiguous provisional candidate. Lock the source during matching and lock each assembly during merge. Persist Telegram edit revisions and include revision in intent identity.

- [ ] **Step 4: Pass assembly tests and existing parser tests**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_assembly.py tests/test_copy_trading_signal_parser.py tests/test_copy_trading_workers.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading/assembly.py src/app/domains/copy_trading/workers.py src/app/domains/copy_trading/engine.py tests
git commit -m "fix(copy-trading): isolate signal conversations"
```

### Task 4: Enforce Trade Readiness And Aggregate Exposure

**Files:**
- Create: `src/app/domains/copy_trading/execution.py`
- Modify: `src/app/domains/copy_trading/service.py`
- Modify: `src/app/domains/accounts/models.py`
- Modify: `src/app/domains/copy_trading/symbols.py`
- Test: `tests/test_copy_trading_execution_safety.py`
- Test: `tests/test_copy_trading_service.py`

- [ ] **Step 1: Write failing tests for investor-only rejection, TP aggregate volume, exposure cap, and stale symbols**

```python
def test_investor_only_account_cannot_activate_route(db, investor_account):
    with pytest.raises(HTTPException) as error:
        activate_route(db, current_user=user, route_id=route.id)
    assert error.value.detail == "Trader access is required for automatic copying."

def test_fixed_each_counts_every_tp_leg():
    assert calculate_total_volume(Decimal("0.1"), 4, "fixed_each") == Decimal("0.4")
```

- [ ] **Step 2: Run tests and verify current fixed-lot checks fail**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_execution_safety.py tests/test_copy_trading_service.py -q`

- [ ] **Step 3: Implement `sync_ready`/`trade_ready`, execution-time caps, MT5 preflight, and catalog hashes**

Require encrypted trader credentials and a recent permissions check. Validate total TP volume, account open copied exposure, broker volume bounds/step, and margin immediately before submission. Recalculate a mapping whenever the live symbol catalog hash differs.

- [ ] **Step 4: Pass safety and service tests**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_execution_safety.py tests/test_copy_trading_service.py tests/test_account_connect_mt5.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading src/app/domains/accounts tests
git commit -m "fix(copy-trading): enforce executable account safety"
```

### Task 5: Add Exact Broker Idempotency In MT5

**Files:**
- Create: `mt5-quant-server/app/mt5_worker/idempotency.py`
- Modify: `mt5-quant-server/app/mt5_worker/handlers/orders.py`
- Modify: `mt5-quant-server/app/mt5_worker/handlers/positions.py`
- Modify: `mt5-quant-server/app/mt5_worker/handlers/accounts.py`
- Create: `mt5-quant-server/tests/test_copy_trading_idempotency.py`

- [ ] **Step 1: Write failing MT5 tests using a fake terminal**

```python
def test_duplicate_client_order_id_returns_existing_order(fake_mt5):
    first = execute_order_job(job(client_order_id="abc123"))
    second = execute_order_job(job(client_order_id="abc123"))
    assert first["result"]["order"]["order"] == second["result"]["order"]["order"]
    assert fake_mt5.order_send.call_count == 1
```

- [ ] **Step 2: Run tests and confirm duplicate submission occurs**

Run: `python -m pytest tests/test_copy_trading_idempotency.py -q`

- [ ] **Step 3: Implement compact client comments and exact lookup**

Encode a stable client ID within MT5's comment limit. Before `order_send`, search positions, orders, history orders, and deals for the exact ID. Reconciliation accepts `client_order_id` and returns the matching item only. Return whether submission started so backend timeout policy can distinguish safe retries.

- [ ] **Step 4: Pass MT5 tests and handler suite**

Run: `python -m pytest -q`

- [ ] **Step 5: Commit in the MT5 repository**

```bash
git add app/mt5_worker tests
git commit -m "fix(copy-trading): make MT5 orders idempotent"
```

### Task 6: Make Backend Execution And Reconciliation Exact

**Files:**
- Modify: `src/app/domains/copy_trading/execution.py`
- Create: `src/app/domains/copy_trading/reconciliation.py`
- Modify: `src/app/domains/copy_trading/workers.py`
- Test: `tests/test_copy_trading_execution.py`
- Test: `tests/test_copy_trading_reconciliation.py`

- [ ] **Step 1: Write failing tests for timeout, duplicate prevention, partial close, lifecycle drift, and emergency locking**

```python
def test_unknown_open_timeout_is_not_blindly_resubmitted():
    reconcile_result = reconcile(intent, broker_result=None)
    assert reconcile_result.state == TradeIntentState.uncertain
    assert publisher.events == []

def test_partial_close_uses_broker_current_volume():
    payload = partial_close_payload(current_volume=Decimal("0.04"), fraction=Decimal("0.5"))
    assert payload["volume"] == 0.02
```

- [ ] **Step 2: Run tests and observe current retry and volume behavior fail**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_execution.py tests/test_copy_trading_reconciliation.py -q`

- [ ] **Step 3: Implement stable intent IDs, exact reconciliation, scheduled lifecycle refresh, and shared locks**

Do not retry an uncertain open unless MT5 reports `submission_started=false`. Reconcile by client ID. Refresh broker state for every active copied trade. Use current broker volume for partial closes. Acquire account locks for emergency operations.

- [ ] **Step 4: Pass execution and reconciliation tests**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_execution.py tests/test_copy_trading_reconciliation.py tests/test_copy_trading_workers.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading tests
git commit -m "fix(copy-trading): reconcile exact broker intents"
```

### Task 7: Harden Telegram Sessions And Learning

**Files:**
- Create: `src/app/domains/copy_trading/telegram_auth.py`
- Modify: `src/app/domains/copy_trading/worker_runtime.py`
- Modify: `src/app/domains/copy_trading/service.py`
- Test: `tests/test_copy_trading_telegram.py`
- Test: `tests/test_copy_trading_runtime.py`

- [ ] **Step 1: Write failing tests for restartable auth, retryable learning, low confidence, and image-only messages**

```python
def test_learning_timeout_does_not_mark_source_unsupported():
    mark_learning_failed(source.id, retryable=True)
    assert source.state == TelegramSourceState.failed_retryable

def test_image_message_creates_warning_and_reclassification_sample():
    result = runtime.handle_message(image_message(text=""))
    assert result == DeliveryResult.success()
    assert activity.action == "source.image_message"
```

- [ ] **Step 2: Run tests and confirm current unsupported/silent behavior fails**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_telegram.py tests/test_copy_trading_runtime.py -q`

- [ ] **Step 3: Persist auth attempts and classify learning outcomes correctly**

Store phone/QR/2FA state and expiry in PostgreSQL. Restore pending flows after worker restart. Treat low confidence as advisory, dependency failures as retryable, and only image-primary classification as unsupported with automatic source pause/disconnect.

- [ ] **Step 4: Pass Telegram and learning tests**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_telegram.py tests/test_copy_trading_runtime.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading tests
git commit -m "fix(copy-trading): make Telegram state durable"
```

### Task 8: Add Health, Metrics, Pagination, And Operational Recovery

**Files:**
- Create: `src/app/domains/copy_trading/health.py`
- Modify: `src/app/domains/copy_trading/router.py`
- Modify: `src/app/domains/copy_trading/repository.py`
- Modify: `src/app/domains/copy_trading/worker_runtime.py`
- Test: `tests/test_copy_trading_health.py`
- Test: `tests/test_copy_trading_router.py`

- [ ] **Step 1: Write failing tests for stale components, server filters, cursor pagination, and dead-letter replay**

```python
def test_stale_execution_worker_makes_health_degraded():
    health = aggregate_health([heartbeat("copy-execution", age_seconds=91)])
    assert health.status == "degraded"

def test_activity_uses_cursor_and_server_filters(client):
    response = client.get("/copy-trading/activity?limit=25&level=error&cursor=abc")
    assert "next_cursor" in response.json()
```

- [ ] **Step 2: Run tests and verify current cosmetic health/list response fails**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_health.py tests/test_copy_trading_router.py -q`

- [ ] **Step 3: Implement persisted heartbeats, aggregate health, metrics, filtered pagination, alerts, and replay API**

Record worker role, instance, observed time, stream lag, pending count, and last error. Expose health without secrets. Log and count email failures. Add cleanup/reconciliation maintenance metrics.

- [ ] **Step 4: Pass health/router tests**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_health.py tests/test_copy_trading_router.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading tests
git commit -m "feat(copy-trading): expose operational health"
```

### Task 9: Build The End-To-End Pipeline Test

**Files:**
- Create: `tests/test_copy_trading_end_to_end.py`
- Create: `tests/fakes/fake_mt5_broker.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write the failing end-to-end scenarios**

Feed Telegram fixtures through a real test Redis stream and PostgreSQL transaction while stubbing only the AI network call and MT5 process boundary. Cover market open, pending order, later SL update, partial close, timeout replay, two simultaneous symbols, and two routes with different windows.

```python
def test_timeout_replay_places_one_broker_order(copy_pipeline):
    copy_pipeline.broker.timeout_after_accepting_once()
    copy_pipeline.publish("BUY EURUSD SL 1.08 TP 1.10")
    copy_pipeline.drain()
    assert copy_pipeline.broker.order_count == 1
    assert copy_pipeline.intent.state == TradeIntentState.confirmed
```

- [ ] **Step 2: Run and confirm the test fails before final integration wiring**

Run: `.venv/Scripts/python -m pytest tests/test_copy_trading_end_to_end.py -q`

- [ ] **Step 3: Wire extracted modules through worker entry points until all scenarios pass**

Replace legacy signal/execution paths only after the new test drives them. Keep compatibility reads for old activity and signal-thread records.

- [ ] **Step 4: Run the full backend suite**

Run: `.venv/Scripts/python -m pytest -q`

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "test(copy-trading): prove complete automation pipeline"
```

### Task 10: Make Frontend State Truthful And Independently Resilient

**Files:**
- Modify: `synctrades-fe/src/features/copy-trading/api.ts`
- Modify: `synctrades-fe/src/features/copy-trading/hooks.ts`
- Modify: `synctrades-fe/src/features/copy-trading/copy-trading-page.tsx`
- Create: `synctrades-fe/src/features/copy-trading/health/copy-system-status.tsx`
- Modify: `synctrades-fe/src/features/copy-trading/overview/monitoring-overview.tsx`
- Modify: `synctrades-fe/src/features/copy-trading/activity/copy-activity-page.tsx`
- Create: `synctrades-fe/src/features/copy-trading/activity/activity-filters-sheet.tsx`
- Modify: `synctrades-fe/src/features/copy-trading/settings/copy-trading-settings-page.tsx`
- Modify: `synctrades-fe/src/features/copy-trading/routes/copy-rules-page.tsx`
- Create: `synctrades-fe/src/features/copy-trading/shared/confirm-action-dialog.tsx`
- Modify: `synctrades-fe/src/features/copy-trading/shared/field-help.tsx`
- Test: `synctrades-fe/src/features/copy-trading/copy-trading-contract.test.ts`
- Test: `synctrades-fe/src/features/copy-trading/copy-trading-view-model.test.ts`

- [ ] **Step 1: Write failing frontend contract/view-model tests**

Assert health status vocabulary, cursor activity API, independent query errors, focused polling, per-row pending state, destructive confirmations, actual route action menu, and one tooltip provider.

```typescript
test("activity uses server pagination", () => {
  assert.match(api, /cursor:\s*params\.cursor/);
  assert.doesNotMatch(page, /events\.filter/);
});

test("one failed query does not blank copy trading", () => {
  assert.doesNotMatch(page, /queries\.some\(\(query\) => query\.isError\)/);
});
```

- [ ] **Step 2: Run tests and verify current global loading/error and 50-row behavior fails**

Run: `node --test src/features/copy-trading/*.test.ts`

- [ ] **Step 3: Implement focused queries, health UI, pagination, responsive filters, row-local saves, menus, and confirmations**

Render each route independently with its own loading/error/empty state. Poll health at 15 seconds and activity only while visible or processing. Use server filtering. Replace “Automation ready” with computed status and actionable component details. Preserve the existing dark design system and compact information density.

- [ ] **Step 4: Run frontend tests, lint, and production build**

Run: `node --test src/features/copy-trading/*.test.ts`

Run: `npm run lint`

Run: `npm run build`

- [ ] **Step 5: Commit in the frontend repository**

```bash
git add src/features/copy-trading
git commit -m "fix(copy-trading): show truthful resilient automation state"
```

### Task 11: Verify, Deploy, Migrate, And Prove Live Behavior

**Files:**
- Modify when required: `Dockerfile`, Railway start commands, and `.env.example`
- Create: `docs/copy-trading-operations.md`

- [ ] **Step 1: Run all repository verification from clean worktrees**

Backend: `.venv/Scripts/python -m pytest -q`

Frontend: `npm run lint && npm run build`

MT5: `python -m pytest -q`

Also run `git diff --check` in every repository.

- [ ] **Step 2: Validate migration upgrade against a disposable PostgreSQL database**

Run Alembic from the previous head through the new head, inspect constraints/indexes, then downgrade only if the migration declares a safe downgrade.

- [ ] **Step 3: Push the three repositories and monitor Railway deployments**

Deploy migration/API first, MT5 second, workers third, and frontend last. Do not expose new public services. Reuse shared database, Redis, AI, email, and MT5 variables.

- [ ] **Step 4: Run live acceptance checks**

Verify fresh worker heartbeats, stream lag, pending counts, dead letters, uncertain intent ages, and health endpoint. Publish a synthetic signal to a dedicated test source/account and confirm one client order ID across activity, intent, MT5 order, copied trade, and reconciliation.

- [ ] **Step 5: Record operational procedures and final evidence**

Document replay, uncertain-intent resolution, image-primary handling, worker alerts, rollback order, and the exact acceptance output in `docs/copy-trading-operations.md`.

- [ ] **Step 6: Commit operational documentation**

```bash
git add docs/copy-trading-operations.md
git commit -m "docs(copy-trading): add reliability operations runbook"
```

## Plan Self-Review

- All 22 audit findings map to at least one task.
- Database changes are additive and preserve active user configuration.
- Every production behavior begins with a failing test.
- Broker timeout handling never assumes failure and never blindly duplicates an order.
- UI readiness derives from backend component health rather than local optimism.
- Completion requires a live synthetic end-to-end result, not merely successful container deployment.

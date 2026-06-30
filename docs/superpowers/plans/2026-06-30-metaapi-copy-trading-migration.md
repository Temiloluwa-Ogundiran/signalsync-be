# Direct MetaApi Copy Trading Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all copy-trading use of the self-hosted MT5 API with independently provisioned, persistent MetaApi streaming connections while leaving journaling unchanged.

**Architecture:** Add a separate copy-account aggregate and provisioning lifecycle, hide the official MetaApi SDK behind application-owned provisioning, connection-manager, and broker interfaces, then migrate routes and durable execution to those interfaces. Cut over atomically behind `COPY_TRADING_METAAPI_ENABLED`, pause legacy routes, and delete copy-specific `Mt5CoreClient` paths after automated and live acceptance.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy/Alembic, Redis Streams, PostgreSQL, `metaapi-cloud-sdk==29.1.1`, Next.js/React/TypeScript, pytest, Vitest, Railway dev.

---

## File Structure

**Backend create:**

- `src/app/domains/copy_trading/metaapi_client.py`: SDK factory, exception classification, and test seam.
- `src/app/domains/copy_trading/metaapi_provisioning.py`: account create/deploy/readiness/delete lifecycle.
- `src/app/domains/copy_trading/metaapi_connections.py`: bounded persistent streaming-connection registry.
- `src/app/domains/copy_trading/metaapi_broker.py`: symbols, execution, terminal-state reconciliation, and result normalization.
- `src/app/domains/copy_trading/client_ids.py`: deterministic MetaApi client-ID encoding.
- `alembic/versions/e1f2a3b4c5d6_add_metaapi_copy_connections.py`: additive schema and route pausing migration.
- `tests/fakes/fake_metaapi.py`: deterministic fake provisioning and streaming boundary.
- `tests/test_copy_trading_metaapi_models.py`
- `tests/test_copy_trading_metaapi_provisioning.py`
- `tests/test_copy_trading_metaapi_connections.py`
- `tests/test_copy_trading_metaapi_broker.py`
- `tests/test_copy_trading_metaapi_end_to_end.py`

**Backend modify:**

- `pyproject.toml`, `uv.lock`: pin official SDK 29.1.1.
- `src/app/core/config.py`, `.env.example`, `docker-compose.yml`: MetaApi settings and feature flag.
- `src/app/domains/copy_trading/models.py`: connection model and connection foreign keys.
- `src/app/domains/copy_trading/schemas.py`: connection and migrated route contracts.
- `src/app/domains/copy_trading/repository.py`: connection persistence queries.
- `src/app/domains/copy_trading/service.py`: user connection lifecycle and route readiness.
- `src/app/domains/copy_trading/router.py`: connection endpoints.
- `src/app/domains/copy_trading/workers.py`: MetaApi broker execution and reconciliation.
- `src/app/domains/copy_trading/worker_runtime.py`: connection lifecycle and provisioning event handling.
- `src/app/domains/copy_trading/symbols.py`: MetaApi specification conversion and volume normalization.
- `src/app/domains/copy_trading/health.py`: MetaApi readiness evidence.
- `scripts/docker_start.py`: MetaApi worker startup role if separated from execution.
- Existing copy-trading tests: migrate journal-account fixtures to copy-connection fixtures.

**Frontend create:**

- `src/features/copy-trading/accounts/metaapi-account-form.tsx`
- `src/features/copy-trading/accounts/metaapi-account-list.tsx`
- `src/features/copy-trading/accounts/metaapi-account-status.tsx`

**Frontend modify:**

- `src/features/copy-trading/types.ts`
- `src/features/copy-trading/api.ts`
- `src/features/copy-trading/hooks.ts`
- `src/features/copy-trading/setup/setup-workspace.tsx`
- `src/features/copy-trading/routes/copy-rule-form.tsx`
- `src/features/copy-trading/routes/copy-rules-page.tsx`
- `src/app/(dashboard)/copy-trading/accounts/page.tsx`
- Copy-trading view-model and contract tests.

## Task 1: SDK, Configuration, And Schema

- [ ] **Step 1: Write failing model/config tests**

Add tests asserting `CopyTradingConnection` fields and enums, `CopyRoute.target_connection_id`, `CopyAccountPolicy.connection_id`, copied-trade/intent connection ownership, and these settings:

```python
assert settings.METAAPI_TOKEN == ""
assert settings.METAAPI_REGION == "london"
assert settings.METAAPI_ACCOUNT_TYPE == "cloud-g2"
assert settings.METAAPI_CONNECTION_TIMEOUT_SECONDS == 120
assert settings.METAAPI_IDLE_CONNECTION_SECONDS == 300
assert settings.COPY_TRADING_METAAPI_ENABLED is False
```

- [ ] **Step 2: Run tests and verify schema assertions fail**

Run: `PYTHONPATH=src pytest tests/test_copy_trading_metaapi_models.py tests/test_copy_trading_models.py -q`

- [ ] **Step 3: Add SDK and settings**

Run: `uv add metaapi-cloud-sdk==29.1.1`

Add the settings above without changing journal `MT5_CORE_*` settings.

- [ ] **Step 4: Add the connection model and additive migration**

Create `CopyTradingConnectionState` and `CopyTradingConnection` with encrypted password, MetaApi IDs, lifecycle states, safe error fields, catalog fingerprint, and health timestamps. Add nullable `target_connection_id`/`connection_id` columns first, pause every legacy route, null legacy copy targets where required, then enforce new foreign keys only after data transformation. Preserve historical IDs on activity and copied-trade records.

- [ ] **Step 5: Run migration/model tests**

Run: `PYTHONPATH=src pytest tests/test_copy_trading_metaapi_models.py tests/test_copy_trading_models.py tests/test_migrations.py -q`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/app/core/config.py .env.example docker-compose.yml \
  src/app/domains/copy_trading/models.py alembic/versions/e1f2a3b4c5d6_add_metaapi_copy_connections.py tests
git commit -m "feat(copy-trading): add MetaApi connection schema"
```

## Task 2: Provisioning Lifecycle And API

- [ ] **Step 1: Write failing provisioning tests**

Cover create payload, `cloud-g2`, high reliability, MT5 platform, unique transaction persistence, deploy/wait lifecycle, identity/trader checks, classified errors, idempotent resume, and undeploy/remove cleanup.

- [ ] **Step 2: Add SDK seam**

`metaapi_client.py` exposes an injectable protocol and a production factory:

```python
def build_metaapi(token: str):
    from metaapi_cloud_sdk import MetaApi
    return MetaApi(token=token)
```

No other application module imports `metaapi_cloud_sdk` directly.

- [ ] **Step 3: Implement provisioning service**

Use `metatrader_account_api.create_account`, `account.deploy()`, `account.wait_deployed()`, `account.wait_connected()`, and account reload. Persist each state transition before the next network call. Never log credentials or tokens.

- [ ] **Step 4: Implement API contracts and ownership checks**

Add:

```text
GET    /copy-trading/connections
POST   /copy-trading/connections
GET    /copy-trading/connections/{id}
POST   /copy-trading/connections/{id}/retry
DELETE /copy-trading/connections/{id}
```

Creation returns `202` with a durable connection. Deletion returns `202` while cleanup runs.

- [ ] **Step 5: Add provisioning/cleanup stream events**

Publish stable idempotency keys to a dedicated provisioning stream consumed by the execution worker process. Replayed events resume persisted state.

- [ ] **Step 6: Run tests**

Run: `PYTHONPATH=src pytest tests/test_copy_trading_metaapi_provisioning.py tests/test_copy_trading_service.py tests/test_copy_trading_router.py -q`

- [ ] **Step 7: Commit**

```bash
git add src/app/domains/copy_trading tests
git commit -m "feat(copy-trading): provision MetaApi accounts"
```

## Task 3: Persistent Streaming Connection Manager

- [ ] **Step 1: Write concurrency and lifecycle tests**

Prove concurrent `acquire(account_id)` calls create one SDK connection, `connect()` and `wait_synchronized()` run once, unhealthy connections are replaced, idle connections close, explicit deletion closes immediately, and shutdown closes all instances.

- [ ] **Step 2: Implement the registry**

Use one process-owned asyncio loop and per-account futures. The production open path is:

```python
account = await api.metatrader_account_api.get_account(metaapi_account_id)
connection = account.get_streaming_connection()
await connection.connect()
await connection.wait_synchronized({"timeoutInSeconds": timeout})
```

Return an application-owned handle exposing `terminal_state` and trade methods. Do not share SDK objects across processes.

- [ ] **Step 3: Connect runtime startup/shutdown**

Create the manager once per execution process, inject it into handlers, and close it in `finally`/signal shutdown paths.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_copy_trading_metaapi_connections.py tests/test_copy_trading_worker_startup.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/app/domains/copy_trading/metaapi_connections.py src/app/domains/copy_trading/worker_runtime.py tests
git commit -m "feat(copy-trading): keep MetaApi connections warm"
```

## Task 4: Client IDs, Symbols, And Broker Adapter

- [ ] **Step 1: Write failing client-ID tests**

Assert deterministic `${strategyId}_${positionId}_${orderId}` output, ASCII/alphanumeric segments, length at most 31, and no collisions across a large fixture set.

- [ ] **Step 2: Write failing broker tests**

Cover all SDK method mappings, options containing `clientId`, `magic`, and comment, normalized result IDs, terminal specifications, volume step rounding, and permanent/transient/uncertain exception classification.

- [ ] **Step 3: Implement client IDs**

Derive compact base32/hash segments from route and intent UUIDs. Store the generated ID on the durable intent and never regenerate it from mutable fields.

- [ ] **Step 4: Implement specification conversion**

Convert MetaApi fields (`symbol`, `contractSize`, `minVolume`, `maxVolume`, `volumeStep`, `tradeMode`, `fillingModes`, `executionMode`) into the application `BrokerSymbol`. Extend resolution evidence and normalize volume before execution.

- [ ] **Step 5: Implement broker actions**

Map to official methods:

```python
create_market_buy_order / create_market_sell_order
create_limit_buy_order / create_limit_sell_order
create_stop_buy_order / create_stop_sell_order
modify_position
close_position_partially / close_position
cancel_order
```

Read `connection.terminal_state.specifications`, `.positions`, and `.orders` for broker truth.

- [ ] **Step 6: Run tests**

Run: `PYTHONPATH=src pytest tests/test_copy_trading_metaapi_broker.py tests/test_copy_trading_symbols.py -q`

- [ ] **Step 7: Commit**

```bash
git add src/app/domains/copy_trading/client_ids.py src/app/domains/copy_trading/metaapi_broker.py \
  src/app/domains/copy_trading/symbols.py tests
git commit -m "feat(copy-trading): add MetaApi broker adapter"
```

## Task 5: Route Ownership And Execution Cutover

- [ ] **Step 1: Migrate service tests to copy connections**

Replace journal-account route fixtures with ready/unready `CopyTradingConnection` fixtures. Prove activation rejects every non-ready state and legacy routes remain paused.

- [ ] **Step 2: Migrate schemas, repository, and service**

Rename API contract fields to `target_connection_id`. Policies use `connection_id`. Keep response compatibility only in the migration release if the frontend deployment order requires it, then remove aliases in the same cutover.

- [ ] **Step 3: Rewrite execution handler against `MetaApiBroker`**

Remove credential decryption and `_submit_mt5`. Load the connection, acquire the Redis account lock, resolve the symbol from MetaApi specifications, execute through the adapter, persist normalized IDs, and record acknowledgement/confirmation latency.

- [ ] **Step 4: Rewrite reconciliation**

Search terminal-state orders and positions plus synchronized history storage by `clientId` and persisted broker IDs. Preserve uncertain state unless broker absence is proven.

- [ ] **Step 5: Add end-to-end fake-MetaApi tests**

Exercise market, pending, modify, break-even, partial close, full close, cancel, retry without duplication, ambiguous acceptance, reconnect, and account isolation through Redis and PostgreSQL fixtures.

- [ ] **Step 6: Run backend copy suite**

Run: `PYTHONPATH=src pytest tests/test_copy_trading*.py -q`

- [ ] **Step 7: Commit**

```bash
git add src/app/domains/copy_trading tests
git commit -m "feat(copy-trading): execute through MetaApi"
```

## Task 6: Frontend Copy Connections

- [ ] **Step 1: Update contract tests first**

Add connection lifecycle types and replace route `target_account_id` expectations with `target_connection_id`.

- [ ] **Step 2: Implement API/hooks**

Add list/create/retry/delete queries and mutations. Poll only nonterminal provisioning states and invalidate routes after readiness/deletion changes.

- [ ] **Step 3: Build the actual accounts experience**

The copy-trading accounts page lists MetaApi connections, provides trader credential connection, renders provisioning/connection/synchronization/trading states, supports retry/delete, and never lists journal accounts as copy targets.

- [ ] **Step 4: Update setup and route forms**

Only ready MetaApi connections are selectable. Existing unlinked routes show a paused migration state and a direct action to connect an account.

- [ ] **Step 5: Run frontend verification**

Run: `yarn test --runInBand` (or the repository's existing Vitest command), `yarn lint`, and `yarn build`.

- [ ] **Step 6: Commit**

```bash
git add src/features/copy-trading src/app/'(dashboard)'/copy-trading
git commit -m "feat(copy-trading): manage MetaApi connections"
```

## Task 7: Health, Dead-Code Removal, And Static Enforcement

- [ ] **Step 1: Add health tests**

Prove health reports MetaApi feature configuration, provisioning, synchronized connections, queue lag, and stale uncertain intents without consulting MT5 worker health.

- [ ] **Step 2: Remove legacy copy MT5 paths**

Delete `_submit_mt5`, copy-specific credentials, endpoint payload builders, MT5 reconciliation paths, and copy execution config. Keep all journal modules and `MT5_CORE_*` settings used outside copy trading.

- [ ] **Step 3: Add static import guard**

Fail when any file under `src/app/domains/copy_trading` contains `Mt5CoreClient` or self-hosted MT5 endpoint strings.

- [ ] **Step 4: Update operations docs and launch script**

Replace terminal/AutoTrading instructions with MetaApi account and WebSocket diagnostics. Update synthetic checks to use the broker interface.

- [ ] **Step 5: Run complete backend/frontend suites**

Run backend pytest, Alembic upgrade/downgrade on a disposable database, frontend tests/lint/build, and dependency audit.

- [ ] **Step 6: Commit**

```bash
git add src tests docs scripts docker-compose.yml .env.example
git commit -m "refactor(copy-trading): remove legacy MT5 execution"
```

## Task 8: Dev Deployment And Live Acceptance

- [ ] **Step 1: Configure dev secrets**

Set `METAAPI_TOKEN`, `METAAPI_REGION`, and `COPY_TRADING_METAAPI_ENABLED=false` on API and execution worker without printing token values.

- [ ] **Step 2: Deploy database, API, execution worker, then frontend**

Observe terminal `SUCCESS` for each Railway deployment. Keep global copy trading paused.

- [ ] **Step 3: Provision demo connections and enable the feature**

Create independent copy connections through the UI/API and verify all readiness states. Enable only the MetaApi feature flag after readiness.

- [ ] **Step 4: Run live acceptance script**

Run ten connection cycles, twenty market open/close cycles, pending create/modify/cancel, SL/TP, break-even, partial/full close, forced reconnect, ambiguous reconciliation, alternate-symbol resolution, and journal regression checks.

- [ ] **Step 5: Prove isolation and latency**

Inspect logs/metrics to prove no copy request reached `mt5-api`; report p50/p95 MetaApi acknowledgement and confirmation latency separately.

- [ ] **Step 6: Push final commits and document evidence**

Push backend/frontend deployed branches only after all checks and live cleanup succeed. Leave no demo positions or pending orders open.

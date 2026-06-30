# Direct MetaApi Copy Trading Migration Design

**Date:** 2026-06-30

**Scope:** `synctrades-be`, `synctrades-fe`, Railway dev services, and MetaApi

## Goal

Move all copy-trading broker connectivity, symbol discovery, execution, and reconciliation from the self-hosted MT5 infrastructure to direct MetaApi WebSocket connections. Keep the self-hosted MT5 API exclusively for journal account connection and synchronization. Preserve the existing Telegram parser, route controls, risk policy, durable intents, audit history, and notifications while removing obsolete copy-trading MT5 code.

## Product Decisions

1. Journaling and copy trading use independent account connections.
2. A journal account is optional for copy trading, and a copy-trading connection is optional for journaling.
3. Users manually connect MT5 trader credentials for copy trading. Existing journal credentials are never reused automatically.
4. Existing copy routes are paused during migration and remain unusable until linked to a ready MetaApi connection.
5. Copy trading has no fallback to the self-hosted MT5 API.
6. The existing Telegram ingestion, signal assembly, deterministic validation, risk rules, durable intents, Redis streams, notifications, and audit records remain application-owned.
7. MetaApi CopyFactory is not used. The application submits its own parsed signals through direct MetaApi streaming connections.

## Architecture

The runtime flow is:

```text
Telegram event
  -> signal parsing and route validation
  -> durable trade intent
  -> account-specific execution queue
  -> persistent synchronized MetaApi WebSocket connection
  -> broker execution
  -> synchronized order/position/deal confirmation
  -> copied-trade and activity records
```

The journal flow remains unchanged and continues through the self-hosted MT5 API. Copy-trading modules must not import or call `Mt5CoreClient` after cutover.

The integration is divided into focused components:

- `MetaApiProvisioningService` creates, deploys, updates, undeploys, and deletes MetaApi accounts.
- `MetaApiConnectionManager` owns persistent synchronized streaming connections for active copy accounts.
- `MetaApiBroker` implements symbols, orders, position management, and reconciliation behind an application-owned interface.
- Existing execution workers continue to own intents, account serialization, persistence, retry policy, and notifications.

MetaApi provisioning uses REST because it is a control-plane operation. Trading, symbol state, and reconciliation use the official Python SDK streaming API because MetaApi recommends WebSockets for lower-latency automated trading and synchronized terminal state.

## Copy-Trading Connections

A new `copy_trading_connections` table represents a user-owned MetaApi connection independently of `trading_accounts`. It stores:

- application ID, user ID, display name, broker login, broker server, and MT platform;
- encrypted trader password;
- MetaApi account ID and idempotent provisioning transaction ID;
- provisioning, deployment, broker-connection, synchronization, permission, and aggregate readiness states;
- the latest actionable error code and safe user-facing message;
- symbol-catalog fingerprint and refresh timestamp;
- last health timestamp, created timestamp, and updated timestamp.

Copy routes reference `copy_trading_connections`. Historical copied trades and activities retain their connection identifiers after disconnection so audit records remain intelligible.

### Connection State Machine

The state progression is:

```text
submitted -> provisioning -> deploying -> connecting -> synchronizing -> ready
```

Terminal failure states are explicit:

- `invalid_credentials`
- `server_not_found`
- `provisioning_failed`
- `broker_disconnected`
- `synchronization_failed`
- `trading_disabled`
- `deleting`
- `deleted`

The API returns quickly with a durable connection record. Background work continues asynchronous MetaApi provisioning when MetaApi returns `202`. The same transaction ID is reused while polling or retrying an accepted provisioning request, preventing duplicate MetaApi accounts.

A connection becomes `ready` only after MetaApi reports deployment, broker connectivity, synchronization, matching login/server identity, and trader permission. Routes cannot activate before readiness.

### Disconnect

Disconnecting a copy connection performs these operations in order:

1. Pause all attached routes.
2. Stop accepting new intents for the connection.
3. Drain or reconcile already-submitted intents.
4. Close local streaming connections.
5. Undeploy and delete the MetaApi account.
6. Remove encrypted credentials and symbol cache.
7. Mark the local connection deleted while retaining copied-trade and activity history.

Failures during MetaApi cleanup leave the connection in `deleting` and are retried idempotently.

## Streaming Connection Management

Execution workers maintain a bounded in-process registry keyed by MetaApi account ID. A connection opens lazily when an active route needs it, waits for synchronization before use, remains warm while active, and closes after a configurable idle period.

The registry deduplicates concurrent connection creation and exposes one readiness future per account. Disconnect events mark the connection unhealthy immediately. Reconnection uses bounded exponential backoff with jitter. Worker shutdown closes SDK connections cleanly. Worker startup rebuilds connections lazily and reconciles unfinished intents before accepting new actions for an account.

The application keeps per-account Redis locks. Different accounts execute concurrently; actions for one account remain serialized.

## Symbols

MetaApi synchronized symbol specifications become the broker catalog. The existing application-owned normalization, aliases, ranking, `SymbolMapping`, and catalog fingerprint logic remain.

Resolution follows these rules:

1. Load the account's complete synchronized specification catalog.
2. Exclude disabled or non-tradable symbols.
3. Normalize the signal symbol and broker symbols.
4. Rank exact and affixed matches using normalized length, contract size, spread when available, and visibility.
5. Validate the chosen symbol immediately before execution.
6. Cache the mapping with the catalog fingerprint.
7. Recompute mappings when the catalog fingerprint changes or the saved symbol stops being tradable.

Execution validates minimum volume, maximum volume, volume step, contract size, filling modes, and execution mode from the selected specification. Manual route symbol overrides remain authoritative only when the overridden symbol is present and tradable.

## Broker Actions

`MetaApiBroker` maps existing actions to SDK streaming operations:

- market buy and sell;
- buy limit, sell limit, buy stop, and sell stop;
- stop-loss and take-profit modification;
- break-even modification;
- partial and full position close;
- pending-order cancellation;
- account positions, orders, deals, and specifications for reconciliation.

Each durable intent receives a compact MetaApi `clientId` in the required `${strategyId}_${positionId}_${orderId}` form and within MetaApi's 31-character limit. The mapping from application intent ID to compact client ID is deterministic and collision-tested. MetaApi order, deal, and position IDs are stored separately on the copied trade.

An intent is marked confirmed only after MetaApi acknowledges the operation and synchronized terminal state provides the expected order, position, or deal evidence. Broker acknowledgement latency and terminal confirmation latency are recorded separately.

## Reliability And Idempotency

The durable intent remains the source of truth for application execution state.

- Deterministic validation and permanent broker rejections fail immediately with precise error codes.
- Rate limits, transient network failures, and temporary MetaApi outages retry with bounded exponential backoff and jitter.
- A timeout or disconnect after submission changes the intent to `uncertain`; it does not immediately submit again.
- Reconciliation searches synchronized orders, positions, and deals using `clientId` and persisted broker identifiers.
- If matching broker state exists, the original intent is confirmed.
- Resubmission is permitted only when reconciliation proves that no matching broker object exists.
- Repeated event delivery, worker restart, and connection replacement cannot create a second broker action for the same intent.
- There is no fallback to the self-hosted MT5 execution API.

User-facing errors distinguish invalid credentials, unknown server, provisioning delay, disconnected broker, synchronization failure, disabled trading, rejected orders, and temporary service failure. Tokens, credentials, SDK internals, and raw transport errors are never returned to clients.

## Health And Observability

Health records and metrics cover:

- MetaApi provisioning/control-plane availability;
- account deployment and broker connection state;
- WebSocket synchronization and reconnect count;
- warm connection count and idle eviction;
- symbol catalog readiness and refresh failures;
- execution worker heartbeat and Redis stream lag;
- intent queue, broker acknowledgement, and confirmation latency;
- rate limits and transient/permanent error counts;
- uncertain intent age and reconciliation outcome;
- duplicate-prevention hits.

Global copy-trading health is degraded when required workers are stale, MetaApi is unavailable beyond its grace period, stream lag exceeds its threshold, or unresolved intents exceed their age threshold. One unhealthy account does not mark unrelated accounts unhealthy.

## Migration And Cutover

The migration is additive until live acceptance succeeds:

1. Add MetaApi configuration, connection storage, provisioning service, and broker interface behind a disabled feature flag.
2. Add the streaming connection manager and MetaApi broker implementation.
3. Adapt routes and execution workers to target copy connections.
4. Run deterministic automated tests and live demo-account tests in dev.
5. Pause existing copy routes globally.
6. Deploy the MetaApi execution worker and enable the feature in dev.
7. Require users to create new copy connections manually.
8. Permit activation only for routes linked to ready MetaApi connections.
9. Observe latency, reconciliation, errors, and duplicate metrics during a guarded rollout.
10. Remove every copy-trading dependency on the self-hosted MT5 API after acceptance.

Rollback pauses MetaApi routes and execution workers. It does not restore copy execution through the self-hosted MT5 service. Journal connectivity and synchronization remain operational throughout migration and rollback.

## Dead-Code Removal

After cutover, remove:

- copy-trading imports and calls to `Mt5CoreClient`;
- copy-specific MT5 payload translation and endpoint paths;
- copy execution/reconciliation configuration for the self-hosted MT5 service;
- obsolete copy-account credential coupling to journal `TradingAccount` records;
- obsolete worker tests, fakes, scripts, environment variables, health checks, and operational documentation;
- migrations or compatibility branches that exist only to select between MetaApi and self-hosted copy execution, once the production schema no longer requires them.

Do not remove self-hosted MT5 code used by account journaling, snapshots, history synchronization, or journal account verification.

## Testing

Automated tests cover:

- idempotent provisioning and MetaApi `202` continuation;
- every connection state and cleanup retry;
- persistent connection reuse, concurrent acquisition, reconnect, idle eviction, and shutdown;
- symbol normalization, aliases, suffixes, prefixes, overrides, fingerprint invalidation, and non-tradable symbols;
- volume normalization and filling/execution-mode validation;
- every supported broker action;
- deterministic, length-safe, collision-resistant client IDs;
- duplicate delivery and worker restart;
- ambiguous submission and reconciliation outcomes;
- rate limits, disconnects, stale synchronization, and permanent broker errors;
- migration route pausing and activation guards;
- complete separation of journal accounts and copy connections;
- preserved audit history after disconnect;
- static enforcement that copy-trading modules do not import `Mt5CoreClient`.

Integration tests run Telegram fixtures through Redis streams, PostgreSQL, the execution worker, and a deterministic fake MetaApi adapter. SDK contract tests mock only the official MetaApi boundary.

## Live Acceptance

Dev acceptance uses demo accounts and requires:

1. Ten consecutive copy-account connections without false failures.
2. Twenty consecutive market-order open/close cycles without duplicates or leaked positions.
3. Successful pending-order create, modify, and cancel.
4. Successful SL/TP modification, break-even, partial close, and full close.
5. Correct automatic alternate-symbol selection.
6. Recovery after execution-worker restart and forced WebSocket interruption.
7. Successful reconciliation of an intentionally ambiguous submission.
8. No effect on journal account connection or synchronization.
9. Evidence that no copy-trading request reaches the self-hosted MT5 API.
10. Warm broker-submission latency below 500 ms at p50 and below one second at p95, measured from durable intent readiness to MetaApi acknowledgement. Broker execution and synchronized confirmation latency are reported separately.

All unit, integration, migration, lint, type, and production-build checks must pass before the feature flag is enabled.

## References

- MetaApi client overview: https://metaapi.cloud/docs/client/
- MetaApi account provisioning: https://metaapi.cloud/docs/provisioning/api/account/createAccount/
- MetaApi trade tracking and `clientId`: https://metaapi.cloud/docs/client/clientIdUsage/
- MetaApi symbol specifications: https://metaapi.cloud/docs/client/models/metatraderSymbolSpecification/

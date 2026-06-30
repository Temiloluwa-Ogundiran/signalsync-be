# Copy Trading Operations

Copy trading executes only through direct MetaApi streaming connections. The self-hosted MT5 service is reserved for journal connection and synchronization.

## Deployment Order

1. Set `METAAPI_TOKEN`, `METAAPI_REGION`, and `METAAPI_ACCOUNT_TYPE` on the API and copy-execution worker.
2. Deploy the migration and API with `COPY_TRADING_METAAPI_ENABLED=false`.
3. Deploy Telegram, signal, and execution workers and confirm all worker heartbeats.
4. Enable `COPY_TRADING_METAAPI_ENABLED=true`, provision demo copy connections, and wait for `ready`.
5. Deploy the frontend, run acceptance, then remove the global pause.

Required health roles are `telegram-session`, `copy-signal`, `copy-execution`, and `copy-provisioning`. Health must also report MetaApi configured.

## Connection Incidents

- `invalid_credentials`: verify the MT5 login, exact broker server, and trader password.
- `server_not_found`: use the exact server shown in the MT5 login window.
- `trading_disabled`: a trader password is required; investor access cannot copy trades.
- `synchronization_failed`: inspect MetaApi deployment, broker connectivity, and WebSocket synchronization.
- `broker_disconnected`: pause only routes attached to the affected connection and reconnect it.

One unhealthy connection must not stop unrelated connections.

## Uncertain Intents

A timeout after submission remains `uncertain`. Never submit a replacement manually. Reconciliation searches synchronized orders, positions, and deals by the persisted MetaApi `clientId` and broker IDs. Confirm matching broker state; otherwise leave the intent uncertain until synchronized absence is proven.

## Failed Events

Transient Redis events remain pending and are reclaimed. After the delivery limit they enter `copy_dead_letters`. Resolve the dependency, replay once through `/api/v1/copy-trading/dead-letters/{id}/replay`, and follow the original correlation ID in Copy Activity.

## Rollback

1. Enable the global copy pause.
2. Disable `COPY_TRADING_METAAPI_ENABLED`.
3. Stop copy execution workers after in-flight intents reconcile.
4. Roll back the frontend and API while retaining the additive migration and audit records.

There is no fallback from copy trading to the self-hosted MT5 service. Journaling remains online throughout rollback.

## Acceptance

- Ten consecutive copy connections reach `ready` without false failures.
- Twenty market open/close cycles produce no duplicates or leaked positions.
- Pending create/cancel, SL/TP, break-even, partial close, full close, and emergency actions succeed.
- Alternate symbols resolve from synchronized MetaApi specifications.
- A forced WebSocket reconnect and ambiguous submission reconcile correctly.
- Copy logs contain no self-hosted MT5 requests.
- Report MetaApi acknowledgement and terminal confirmation p50/p95 separately.
- Leave no demo positions or pending orders open.

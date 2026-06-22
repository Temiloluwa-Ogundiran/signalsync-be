# Copy Trading Operations

## Deployment Order

1. Deploy the API image and run `alembic upgrade head` once.
2. Deploy the MT5 worker with exact `client_order_id` reconciliation support.
3. Redeploy the Telegram session, learning, signal, and execution workers.
4. Confirm `/api/v1/copy-trading/health` reports every required role.
5. Deploy the frontend after the backend health and paginated activity contracts are live.

Do not deploy the execution worker before the MT5 worker. The backend intentionally leaves unknown broker submissions uncertain instead of submitting them twice.

## Required Worker Roles

The health endpoint expects fresh heartbeats from:

- `telegram-session`
- `copy-learning`
- `copy-signal`
- `copy-execution`

A heartbeat older than 60 seconds is stale. A missing role produces `action_required`; a stale or degraded role produces `degraded`.

## Failed Event Recovery

Transient events remain pending in Redis and are reclaimed by the worker. After the retry limit, the event is stored in `copy_dead_letters` before Redis acknowledgement.

1. Resolve the dependency failure shown in `error_code` and `error_message`.
2. Use `GET /api/v1/copy-trading/dead-letters` to locate the user-owned event.
3. Use `POST /api/v1/copy-trading/dead-letters/{id}/replay` once.
4. Follow the original `correlation_id` in Copy Activity.

Replay creates a new idempotency key. Broker opens remain protected by the original stable `client_order_id`.

## Uncertain Broker Intents

An open order that times out after submission is marked uncertain. Never manually retry it first.

1. Query the MT5 reconciliation endpoint with the exact `client_order_id`.
2. Search positions, active orders, order history, and deals for the compact `cpid:` comment.
3. If found, allow reconciliation to confirm the intent and copied trade.
4. If MT5 explicitly reports that submission never started, the event may be retried.
5. Escalate unresolved uncertain intents instead of creating a replacement order.

## Channel Learning Outcomes

- Low confidence is advisory. Users may activate copy rules and should review activity closely.
- Dependency timeouts are retryable and leave the source available for another analysis.
- Image-primary channels are unsupported in v1 and are automatically paused after classification.
- Raw samples are retained for seven days according to the configured cleanup job.

## Continuous Reconciliation

The execution worker refreshes copied positions and pending orders every 30 seconds. It updates current volume, stop loss, take profit, broker state, and synchronization time. Partial closes always use current broker volume.

Alert when any of these persist:

- execution heartbeat older than 60 seconds
- Redis pending count increasing for five minutes
- stream lag increasing for five minutes
- dead letters not replayed or resolved
- uncertain intents older than two reconciliation cycles

## Rollback

1. Pause Copy Trading globally before rolling back workers.
2. Roll back the frontend first.
3. Roll back signal and execution workers.
4. Roll back MT5 only after no new-version execution worker remains.
5. Keep the additive database migration in place. Its tables and columns are backward compatible and preserve audit data.

Do not downgrade the database while copied trades, route assemblies, dead letters, or uncertain intents created by this release exist.

## Acceptance Checklist

- All four worker roles report `healthy`.
- Stream lag and pending counts are stable.
- No unexpected pending dead letters exist.
- A test source creates one correlation ID and one stable client order ID.
- A simulated timeout after broker acceptance results in one MT5 order.
- A later stop-loss update changes the same copied position.
- A partial close reduces `current_volume` without changing `original_volume`.
- Activity server filters and cursor pagination return the expected event chain.

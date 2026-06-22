# Copy Trading Reliability Overhaul Design

**Date:** 2026-06-22
**Status:** Approved for implementation
**Scope:** `synctrades-be`, `synctrades-fe`, and `mt5-quant-server`

## Objective

Turn Copy Trading from an optimistic message-processing prototype into a production-grade financial automation system. A Telegram message must be isolated into the correct signal, evaluated independently for every copy rule, converted into durable trade intents, executed exactly once from the user's perspective, reconciled against broker truth, and represented honestly in the UI.

This overhaul fixes all 22 findings from the June 22 audit without deleting existing Telegram sessions, sources, copy rules, or activity history.

## Non-Negotiable Invariants

1. Messages for different symbols, directions, Telegram reply chains, or explicit signal references never share signal state.
2. Every copy rule owns its assembly state and deadline. One rule cannot complete, delay, expire, or corrupt another rule.
3. A transient dependency failure never becomes a successful acknowledgement. It is retried with bounded backoff and eventually becomes a visible replayable dead letter.
4. One `TradeIntent` maps to at most one broker operation. Broker retries reuse a stable client correlation identifier.
5. Unknown broker outcomes remain uncertain until intent-specific reconciliation proves success or an operator resolves them. An uncertain open is never blindly resubmitted.
6. Only MT5 accounts with verified trader credentials can receive copied trades. Investor credentials remain valid for read-only synchronization only.
7. Position volume, lifecycle, SL, TP, pending state, and close state are periodically reconciled from broker truth.
8. Account limits apply to aggregate order volume and open copied exposure, not merely the configured lot of one leg.
9. Health shown to users is computed from fresh Telegram, signal-worker, execution-worker, AI, Redis, database, and MT5 evidence.
10. The system exposes sufficient metrics and activity evidence to explain every message decision and every broker outcome.

## Architecture

### Signal Identity And Assembly

Introduce `SignalConversation` as the durable signal identity and `RouteSignalAssembly` as per-route state. Conversation matching uses, in priority order:

1. Telegram reply root or explicit referenced message.
2. An active conversation with matching normalized symbol and direction.
3. A provisional conversation for incomplete messages when there is exactly one unambiguous candidate.
4. A new conversation.

Ambiguous messages do not mutate an existing trade. They produce a visible `signal.ambiguous` activity event and wait for a clarifying message until expiry. Database row locking and a uniqueness constraint prevent two active assemblies for the same route and conversation. Edited messages carry a monotonic Telegram revision and create a new parsed action and intent version when materially changed.

Each route has its own deadline, minimum fields, confidence decision, entry-without-SL policy, and lifecycle. Completing one route does not close the shared conversation for other routes.

### Durable Event Processing

Redis streams remain the transport, with these changes:

- Consumer handlers return an explicit `success`, `retry`, or `dead_letter` outcome.
- Transient errors stay pending and are retried with exponential backoff and a maximum attempt count.
- Permanent validation failures are acknowledged only after durable activity is committed.
- Dead letters are stored in PostgreSQL with payload, attempt count, error class, worker, timestamps, and replay status.
- Redis streams use approximate `MAXLEN` retention and a scheduled cleanup policy.
- Telegram commands use the same durable outcome protocol; authentication progress is persisted in PostgreSQL rather than worker memory.
- Per-source ordering locks protect conversation assembly while account locks protect execution.

### Broker Execution And Idempotency

Every trade intent receives a stable `client_order_id` included in the MT5 comment using a compact encoded form. MT5 action handlers first search current orders, positions, and recent history for that identifier before submitting. Repeated requests return the existing broker result.

For a transport timeout:

- Open and pending-order intents move to `uncertain`.
- Reconciliation searches for the exact `client_order_id`, not merely route magic/comment or recency.
- If not found, the intent remains uncertain and is retried only when the MT5 worker can prove the original request never reached order submission.
- Management actions reconcile against the exact broker ticket and expected resulting state.

`CopiedTrade` stores original volume, current volume, broker IDs, SL, TP, state, and last broker synchronization time. Partial closes use current broker volume. Emergency actions acquire the same account lock as normal execution.

### Account And Exposure Safety

Trading accounts expose separate `sync_ready` and `trade_ready` states. Creating or activating a route requires `trade_ready`, fresh MT5 health, and a successful lightweight permissions check.

Account policy enforcement occurs again immediately before broker submission. It validates:

- Total volume across all TP legs.
- Per-order volume.
- Aggregate copied open exposure.
- Broker min/max/step constraints.
- Available margin returned by MT5 preflight.

Symbol mappings are versioned by a hash of the live broker catalog. Execution revalidates tradability and contract metadata; stale mappings are recalculated automatically.

### Continuous Reconciliation

A scheduled reconciliation worker refreshes active copied positions and pending orders. It updates local lifecycle state for manual broker changes, SL/TP execution, pending activation, partial closes, and external cancellation. Management-message matching considers only broker-confirmed open or pending trades and prefers exact conversation identity before the most recent symbol match.

### Learning And Unsupported Content

Channel learning has distinct `ready`, `advisory`, `failed_retryable`, and `unsupported_image_primary` outcomes. Low confidence and transient failures never disable copying. Image-primary channels are disconnected with a clear activity event. Runtime image-only messages produce a warning and contribute to reclassification instead of disappearing silently.

### Health, Metrics, And Operations

Persist worker heartbeats and expose a health endpoint with component freshness. Add metrics for stream lag, pending count, retry count, dead letters, parsing latency, assembly duration, execution latency, uncertain intent age, broker success rate, Telegram session freshness, and reconciliation drift.

Dead letters receive API and UI surfaces for inspection and replay. Alerting is triggered for stale workers, old uncertain intents, dead-letter growth, and repeated Telegram disconnects. Email failures are logged and metered rather than swallowed.

## Frontend Design

Copy Trading pages load independently. Failure of activity history does not hide routes or safety controls. Query polling is replaced with focused freshness intervals and invalidation after mutations.

The top status becomes a component health summary instead of “Automation ready.” It distinguishes:

- Ready to copy
- Setup incomplete
- Degraded
- Paused
- Action required

Activity history uses server-side pagination and filters and no longer claims permanence without displaying the full history. Execution events show explicit stages: received, analyzing, waiting for details, ready, submitting, confirming, completed, skipped, or failed.

Rule actions use a real menu. Destructive actions require confirmation. Account settings save independently per row and synchronize local form state after server refresh. Mobile filters use a sheet; long dialogs use a stable footer with the primary action visible. Tooltips share one provider and remain accessible by keyboard.

## Data Migration

Additive migrations introduce conversation identity, route assemblies, Telegram auth attempts, durable dead letters, worker health, intent client IDs, copied-trade volume/state fields, and symbol catalog hashes. Existing `SignalThread` records remain readable for audit but new messages use the new model. Existing active routes are preserved and revalidated; routes targeting investor-only accounts become `needs_attention` rather than being deleted.

Migration order is backward compatible:

1. Deploy additive schema.
2. Deploy readers that understand old and new records.
3. Deploy workers using new records and stable broker identifiers.
4. Deploy frontend health and pagination UI.
5. Enable continuous reconciliation and stream retention.

Rollback disables new workers while preserving all added records. No destructive migration is part of this release.

## Testing Strategy

Backend tests cover conversation routing, replies, simultaneous symbols, multi-route windows, edited messages, transient retry, dead-letter replay, authentication restart, image messages, exposure caps, trade-ready checks, symbol invalidation, and lifecycle reconciliation.

MT5 tests cover stable client IDs, duplicate request replay, exact-intent reconciliation, order-check failures, partial-close volume, and timeout ambiguity.

Frontend tests cover independent page errors, health statuses, paginated activity, per-row saves, action menus, confirmations, responsive filters, and accessible tooltips.

An end-to-end test runs Telegram-event fixtures through Redis, AI parser fixtures, PostgreSQL, execution workers, and a deterministic fake MT5 broker. It proves one complete market order, one pending order, an action-based SL update, a partial close, retry without duplication, and multi-route isolation.

## Deployment And Acceptance

All unit, integration, migration, type, lint, and production-build checks must pass. Railway deployment proceeds database first, then MT5, backend API, workers, and frontend. Acceptance requires fresh heartbeats for every worker, zero pending migrations, zero old uncertain intents, bounded stream lag, successful dead-letter replay, and a synthetic end-to-end copied trade with the same `client_order_id` visible through Telegram ingestion, activity, intent, MT5 result, and reconciliation.

The feature is not considered complete merely because containers are online.

## Audit Coverage

The design addresses all audit findings: signal mixing, per-route assembly, duplicate execution risk, swallowed parser failures, investor-account activation, partial-close sizing, assembly races, stale broker lifecycle, sequential parsing/backpressure, learning misclassification, silent image messages, lossy Telegram commands, misleading lot caps, stale symbol mappings, unlocked emergency actions, unbounded streams, cosmetic health, truncated activity, global page failure, wasteful polling, deceptive route controls, and missing end-to-end verification.

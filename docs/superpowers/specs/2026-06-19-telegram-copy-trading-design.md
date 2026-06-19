# Telegram Copy Trading Design

**Date:** 2026-06-19

**Status:** Approved design

**Scope:** Fully automated Telegram-to-MT5 copy trading, v1

## Objective

Allow a user to connect a read-only Telegram user session, select channels or groups, and automatically execute supported text trade instructions on one or more connected MT5 accounts. The system must learn each source's signal style, process complete and multi-message signals, submit complete instructions to MT5 in under one second under normal conditions, and remain duplicate-safe through retries, restarts, and uncertain broker responses.

## Success Criteria

- A complete eligible Telegram text message normally reaches MT5 submission in under one second.
- Invalid credentials, invalid orders, and permanent failures reach the user immediately and never create a successful-looking account or trade.
- Duplicate Telegram delivery, worker retries, restarts, and uncertain MT5 responses do not create duplicate broker actions.
- Channel-specific parsing can be learned once, quickly revalidated, and safely reused by later users.
- Every automated broker action is traceable to its Telegram source, parsed instruction, route settings, durable intent, and MT5 result.
- Users can stop automation at every meaningful scope without changing existing broker state.

## V1 Boundaries

V1 supports:

- Telegram channels and groups accessible through a user session
- Text and useful image-caption signals
- Market and pending orders
- Fixed-lot sizing
- Multiple take-profit strategies
- Automated SL/TP changes, break-even, partial/full close, and pending cancellation
- One source copied to multiple MT5 accounts through independent routes

V1 excludes:

- Image/OCR signal extraction
- Risk-percentage or balance-proportional sizing
- Reverse trading
- User-authored parsing rules or regex parsers
- Manual retry controls
- Automatic actions for informational `TP hit` or `SL hit` messages

Sources that primarily depend on image signals are marked unsupported and automatically disabled. Occasional images are ignored unless their captions contain sufficient text.

## Architecture

SyncTrades remains the control plane for users, Telegram connections, sources, routes, settings, activity, notifications, and emergency controls. The execution path is divided into isolated workers with explicit contracts.

### Components

1. **Telegram Session Worker**
   - Owns encrypted read-only Telegram user sessions.
   - Supports phone-code, QR, and Telegram 2FA login.
   - Never stores the 2FA password.
   - Receives messages and edits, filters eligible sources/authors, and publishes normalized message events.

2. **Signal Engine**
   - Loads the reusable learned channel profile.
   - Links messages to signal threads using replies, explicit identifiers, symbol/direction, and recency.
   - Uses the fastest suitable small AI model for hot-path parsing.
   - Produces a structured action and confidence score; it never calls MT5 directly.

3. **Validation Engine**
   - Applies deterministic checks after AI parsing.
   - Enforces confidence thresholds, required fields, route settings, lot validity, supported actions, deduplication, symbol mapping, and pause state.

4. **Execution Coordinator**
   - Writes a durable trade intent before broker communication.
   - Serializes actions for each MT5 account while allowing different accounts to execute concurrently.
   - Fans one signal out independently across routes and accounts.

5. **MT5 Worker**
   - Owns MT5 sessions and broker reconciliation.
   - Resolves and selects symbols, submits actions, and returns broker order/deal identifiers.
   - Tags copied orders with a stable magic number and short route identifier.

6. **Activity and Notification Workers**
   - Persist enriched activity events and send email outside the critical execution path.
   - Never delay broker submission for presentation or notification work.

### Transport And State

A dedicated high-priority Redis event stream carries copy-trading events instead of the general Celery workload. PostgreSQL is the source of truth for sessions, profiles, routes, signal state, durable intents, copied trades, and activity. Redis provides event transport, short-lived caches, account locks, deduplication acceleration, and pause-state lookup.

The execution path is:

`Telegram event -> signal parse -> deterministic validation -> durable intent -> MT5 submission -> durable result`

Email, analytics, and activity enrichment occur after the durable broker result.

## Domain Model

### Telegram And Learning

- `telegram_connections`: owner, encrypted session, Telegram identity, authorization state, heartbeat, and reauthentication status.
- `telegram_sources`: Telegram connection, chat identity, source type, access state, content-support state, and channel profile.
- `channel_profiles`: Telegram chat identity, parser version, signal style, recommended assembly window, confidence, image frequency, analyzed range, sample counts, and validation status.
- `channel_message_samples`: encrypted raw sample, Telegram metadata, collection purpose, and expiration time.

### Routing And Signals

- `copy_routes`: source, MT5 account, activation/pause state, fixed lot, optional symbol overrides, TP strategy, pending-order policy, minimum fields, management permissions, author filter, assembly-window override, and notification settings.
- `signal_threads`: source-level logical signal, assembly deadline, state, extracted context, and related message references.
- `parsed_actions`: immutable parser output, parser/model version, confidence, extracted fields, source references, and validation result.
- `trade_intents`: idempotency key, route, action, payload, state, attempt metadata, and broker-resolution state.
- `copied_trades`: route, magic number, route comment, symbol mapping, broker order/deal/position identifiers, lifecycle state, and originating signal.
- `copy_activity`: permanent user-facing audit event, settings snapshot, parsed details, MT5 request/result metadata, timestamps, and correlation ID.
- `symbol_mappings`: route/account, normalized signal symbol, broker symbol, selection evidence, catalog version, and timestamps.

### Retention And Privacy

- Learned channel profiles are reusable between users after quick validation.
- Raw learning samples are encrypted and retained for seven days.
- The privacy policy must disclose Telegram message processing and temporary raw-message storage.
- Another user's identity, Telegram connection, and private-source data are never exposed through a shared profile.
- Parsed actions, execution details, settings snapshots, and activity records are retained while the SyncTrades user account exists.
- Raw Telegram text is hidden by default and may be revealed only to the owning account through an explicit debug control.
- Notification emails contain parsed/executed trade details, never raw Telegram messages.

## Channel Onboarding And Learning

Users may connect Telegram and learn sources before connecting MT5. Automation starts only after an active route has a valid target account.

For a newly selected source:

1. Scan the previous seven days of accessible messages.
2. Reuse an existing channel profile when available, then run a quick fresh validation.
3. Detect complete versus multi-message style, replies/threads, author patterns, assembly timing, image dependence, supported action language, and confidence.
4. Store the learned profile and short-lived encrypted samples.
5. Present signal style, recommended assembly window, image frequency, and automation confidence.
6. Permit automatic operation for medium- and high-confidence text sources; block low-confidence and unsupported sources.

Confidence thresholds are system-controlled. Medium-confidence sources run without an extra warning. Channel assembly windows are learned automatically. A user may increase the route window up to ten minutes but cannot reduce it below the learned safety minimum.

Groups process administrator/channel-originated messages by default. A route setting may allow all group authors. Forwarded messages are processed normally.

## Signal Assembly And Interpretation

Every eligible message is treated as a possible action in an evolving signal thread. A source does not have to publish all fields at once.

Examples:

- `Buy EURUSD` may open once the route's minimum fields are satisfied.
- A later `SL 1.0750` modifies the matching copied trade.
- A later `TP2 1.0900` modifies or creates a new TP leg according to route settings.
- A clear close or partial-close instruction may act automatically when enabled.

Matching priority is:

1. Telegram reply/thread relationship
2. Explicit signal identifier or quoted reference
3. Symbol and direction
4. Most recent matching copied trade

When an action becomes complete, it executes immediately; the system does not wait for the assembly deadline. Incomplete signals expire at the deadline. Message edits are reparsed and diffed against prior actions. Deletion before execution expires the setup; deletion after execution creates an alert and audit event but does not reverse the broker action.

AI is the only parser in v1. Deterministic validation still controls execution: required fields, numeric sanity, allowed action, confidence, duplicate state, route configuration, symbol validity, and pause state. Unsafe minimum-field choices are allowed only after warning confirmation and remain visibly risk-badged.

The default minimum opening fields are direction, symbol, stop loss, and take profit. Supported choices are:

- Direction and symbol
- Direction, symbol, and entry
- Direction, symbol, and stop loss
- Direction, symbol, and take profit
- Direction, symbol, stop loss, and take profit

## Trading Rules

### Sizing

- V1 uses fixed lots only.
- Fixed lot is configured per route, with optional symbol overrides and an account-level maximum.
- Broker-invalid lot sizes are rejected and reported; they are never silently rounded.

### Market And Entry-Zone Signals

- Complete market instructions submit immediately.
- Entry-zone instructions execute only when current price is inside the stated zone; otherwise they are skipped.
- Immediate-entry signals older than the system-controlled freshness threshold, initially about 30 seconds, are skipped.

### Pending Orders

- Pending orders are supported automatically and may be disabled per route.
- A valid pending order is sent to the broker immediately, never held inside SyncTrades waiting for price.
- Orders are GTC unless the source explicitly provides an expiration.
- Clear cancellation instructions cancel matching pending orders when management permission allows it.

### Multiple Take Profits

Routes choose one of:

- Place every TP
- Place only the lowest TP
- Place only the highest TP

When placing every TP, routes choose either:

- Split one total fixed lot across TP positions, available only when multiple TP placement is enabled.
- Apply the configured fixed lot independently to every TP position.

Additional TP messages may create additional positions using known signal parameters. The coordinator must prevent an edited/repeated TP message from duplicating an existing leg.

### Automated Management

Per-route permissions control:

- Stop-loss and take-profit changes
- Break-even changes
- Additional TP legs
- Partial close
- Full close
- Pending-order cancellation

Informational messages such as `TP hit` or `SL hit` never initiate a broker action. Reverse trading is absent from v1. Manual MT5 edits do not detach the copied trade; management continues until the user explicitly detaches it.

## Symbol Resolution

After MT5 connection, the worker retrieves the full broker catalog with `symbols_get()`, including symbols not visible in Market Watch. Cached metadata includes symbol name, aliases, contract size, lot limits and step, digits, point, trade mode, supported order types, visibility, and spread.

Resolution follows this order:

1. Normalize the source symbol and known aliases.
2. Remove disabled, close-only, and incompatible instruments.
3. Prefer the closest normalized broker-symbol match.
4. If multiple valid candidates remain, prefer the candidate with the largest contract size, then use spread and availability as tie-breakers.
5. Persist the automatic mapping per route and MT5 account.
6. Call `symbol_select(symbol, true)` before submission.

No user confirmation interrupts execution. Catalogs refresh on connection, daily, manually, and after symbol-not-found errors.

## Idempotency, Ordering, And Reconciliation

### Idempotency

The base message identity is Telegram connection, chat ID, message ID, route, and action type. Message edits update the existing logical action. Identical text sent as a new Telegram message is a new signal. Every durable intent has a stable idempotency key, and every copied order carries the route magic number and route comment.

### Ordering

Actions targeting one MT5 account are serialized to preserve broker state and prevent conflicting modifications. Different accounts execute concurrently. Multi-account fanout is independent: one account's failure does not delay or roll back another account.

### Uncertain Outcomes

There is no manual retry button. A temporary failure may retry automatically only after broker reconciliation confirms that the original action was not accepted. Invalid credentials, validation errors, invalid lots, unsupported symbols, and explicit broker rejections fail immediately without retry.

After worker startup or an outage, each affected account enters `reconciling`. Before accepting new actions, the worker compares unresolved intents, copied positions, pending orders, magic numbers, comments, order/deal history, and known broker identifiers. It then resolves each intent as succeeded, safely retryable, failed, or requiring operator investigation.

## State Models

### Route State

`draft -> learning -> ready -> active`

Any active route may enter `paused`, `reauthentication_required`, `unsupported`, or `target_unavailable`. Resuming requires the underlying connection, profile, route, and target account to be valid.

### Signal State

`assembling -> complete -> validated -> executing -> succeeded`

Terminal alternatives are `expired`, `skipped`, `rejected`, and `failed`. A message edit may create a new parsed action revision but never rewrites completed audit history.

### Intent State

`created -> submitted -> confirmed`

Exceptional states are `uncertain`, `reconciling`, `retryable`, and `failed`. Only reconciliation may move `uncertain` to `confirmed` or `retryable`.

## Settings And Safety

Every copy route has independent source-to-account settings. Cached settings and pause state are invalidated immediately after changes.

Pause scopes are:

- Entire SyncTrades user
- Telegram connection
- Telegram source
- Copy route
- MT5 target account

Pausing stops all future automated actions, including modifications, closes, and cancellations. It leaves broker positions and pending orders untouched.

Emergency actions support:

- Close all copied positions
- Cancel all copied pending orders
- Perform both actions

Emergency scope may be global, account, source, or route. Every emergency action requires confirmation and produces permanent activity and email records.

## Activity And Notifications

The Copy Trading Activity page provides:

- A signal-grouped timeline from detection through final broker outcome
- A global chronological feed
- Filters for source, route, MT5 account, status, symbol, and action

User-facing status language describes the task rather than infrastructure, for example:

- `Signal detected`
- `Analyzing instructions`
- `Waiting for missing take profit`
- `Trade opened`
- `Stop loss updated`
- `Skipped: price outside entry zone`
- `Reconnecting`
- `Confirming broker status`
- `Failed: broker rejected the order`

Internal queue names, worker details, and raw exceptions are never shown. Permanent failures appear immediately in the UI and trigger email. Every successful broker action also triggers email: open, pending placement, modification, cancellation, partial close, and full close. Emails contain parsed and executed details only.

## Performance Design

The hot path caches active Telegram sessions, source eligibility, channel profiles, route settings, symbol mappings, MT5 metadata, and pause state. Writes required for correctness occur before MT5 submission; nonessential enrichment occurs asynchronously.

Primary latency measurements are:

- Telegram receipt to normalized event
- Event to parsed action
- Parsed action to validated durable intent
- Intent to MT5 submission
- Submission to broker acknowledgement

The normal target is under one second from receipt of a complete text signal to MT5 submission. Channel learning, history scanning, email, journal synchronization, and analytics never run on this path.

## Failure Handling

- **Telegram authorization lost:** pause affected routes and request reauthentication.
- **Unsupported image source:** mark unsupported, notify the user, and disable automation.
- **Low parsing confidence:** do not execute; record a clear skipped/rejected event.
- **Missing fields:** keep assembling until the deadline, then expire visibly.
- **Invalid credentials or account:** fail immediately and never display the route/account as successfully connected.
- **Invalid lot or unsupported order:** reject immediately with broker-relevant guidance.
- **MT5 timeout or connection loss:** mark uncertain, reconcile, then retry only if absence is proven.
- **Worker restart:** reconcile affected accounts before processing new actions.
- **Partial fanout failure:** preserve independent results for every target account.

## Observability

Every event shares a correlation ID from Telegram message through parsed action, intent, broker result, activity, and email. Operational telemetry includes:

- Per-stage latency percentiles
- Active and unhealthy Telegram sessions
- Parsing confidence and rejection rates by channel profile/version
- Intent age and unresolved uncertain intents
- Duplicate prevention counts
- Broker rejection categories
- MT5 worker heartbeats and reconciliation duration
- Route/source pause and unsupported-source counts

Operator alerts fire for unresolved intents, stale reconciliation, worker heartbeat loss, duplicate-suppression anomalies, and sustained latency-budget breaches.

## Testing Strategy

### Parser Fixtures

Cover complete signals, fragmented signals, replies, edits, forwards, author filtering, extra commentary, cancellations, ambiguous references, and different channel styles.

### Execution Contract Tests

Cover market and pending orders, entry zones, TP selection and splitting, SL/TP changes, additional legs, partial/full close, pending cancellation, invalid lots, symbol mapping, route permissions, and broker rejections.

### Failure Simulations

Cover Telegram disconnect, AI timeout, Redis restart, worker crash, MT5 outage, uncertain broker response, duplicate event delivery, stale account locks, and restart reconciliation.

### End-To-End Tests

Exercise Telegram event through durable intent and mocked MT5 execution, including message edits, multi-account fanout, independent target failures, activity visibility, email dispatch, and kill switches.

## Rollout

Release behind feature flags in three stages:

1. Internal accounts and controlled channels
2. Selected external channels and accounts
3. Broader activation after latency, duplicate-order, rejection, and reconciliation targets remain healthy

Automation must not expand beyond each stage until no unexplained duplicate broker actions exist and uncertain intents reliably reconcile.

## Implementation Decomposition

This design spans several independently testable delivery slices. The implementation plan should sequence them as:

1. Domain schema, route settings, permissions, and activity contracts
2. Telegram authentication, encrypted sessions, source selection, and ingestion
3. Channel learning, profile reuse, validation, and raw-sample retention
4. Signal assembly, AI parser contract, and deterministic validation
5. Durable intents, account serialization, idempotency, and reconciliation
6. MT5 execution, symbol catalog/mapping, and trade management
7. Activity UI, notifications, pause controls, and emergency actions
8. Reliability tests, observability, feature flags, and staged rollout

Each slice must preserve the durable audit chain and must not introduce an execution path that bypasses validation or intent persistence.

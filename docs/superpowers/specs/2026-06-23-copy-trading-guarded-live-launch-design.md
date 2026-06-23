# Copy Trading Guarded Live Launch Design

**Date:** 2026-06-23
**Status:** Approved
**Scope:** `synctrades-be`, `synctrades-fe`, `mt5-quant-server`, and Railway production operations

## Objective

Prepare Copy Trading for a guarded live-money launch by making split-signal handling deterministic, ensuring every broker operation reaches a terminal and auditable state, removing obsolete historical-learning behavior, and enforcing operational release gates.

The launch is guarded rather than unrestricted. New users may trade live, but account exposure limits, dependency readiness, global pause controls, and incident response procedures are mandatory. A healthy container is not sufficient evidence that automation is safe.

## Product Decisions

1. Historical channel learning is not part of signal eligibility, channel support, routing, or execution.
2. Every selected Telegram channel or group is accepted, including empty channels.
3. Text messages are interpreted at runtime using the active conversation and copy-rule settings.
4. Image-only messages are skipped and recorded, but they never disable or disconnect a channel.
5. AI confidence is advisory. It does not block a structurally complete, unambiguous signal.
6. Ambiguous instructions do not move money.
7. Users may require SL, TP, entry, or combinations of those fields before opening a trade.
8. If required details do not arrive during the configured assembly window, the signal expires without execution.
9. If the user explicitly allows entry without SL or TP, later instructions may modify the copied trade.
10. Copy trading requires verified trader access. Investor access remains read-only.

## Signal And Conversation Model

### Conversation Identity

A `SignalConversation` represents one logical trade discussion in one Telegram source. Conversation matching uses this order:

1. Telegram reply root or explicit message reference.
2. Explicit symbol and direction matching an active conversation.
3. Explicit symbol matching one active conversation.
4. The only recent incomplete conversation when no conflicting candidate exists.
5. A new conversation.

If more than one candidate remains, the message is marked ambiguous. It is stored in activity history and does not mutate any trade.

### Persistent Opening Intent

The opening intent is persistent conversation state, separate from the latest message action.

Example:

1. `BUY XAUUSD` creates an opening intent with direction and symbol.
2. `SL 2315` enriches that opening intent with a stop loss.
3. The latest message may be classified as an SL update, but it cannot replace the conversation's opening action while the opening generation is incomplete.
4. When the route's required fields are complete, the system creates the opening trade intent.

This prevents a later SL or TP message from converting an incomplete opening signal into a modification request for a trade that does not yet exist.

### Execution Generations

Each route and conversation owns numbered execution generations.

- Generation 1 is the initial market or pending order.
- A materially edited opening signal supersedes an unsubmitted generation.
- After a generation is submitted, edits create corrective management actions rather than a duplicate opening.
- Additional TP instructions create a new generation only when the route permits additional TPs.
- SL/TP, break-even, partial-close, full-close, and cancellation instructions target a broker-confirmed copied trade.

Every generation reaches one terminal state:

- `completed`
- `expired`
- `skipped`
- `failed`
- `superseded`

No generation remains indefinitely in `executing`.

### Conversation Completion

A conversation remains available for permitted management actions after its opening generation completes. It is no longer eligible for another ordinary open action.

The conversation closes when:

- all copied positions and pending orders linked to it are closed or cancelled and the quiet period expires;
- the source instruction explicitly closes the trade;
- an operator archives it after a permanent failure.

This separates "opening completed" from "trade discussion closed."

## Assembly And Entry Policy

Each copy rule declares its required opening fields:

- direction and symbol;
- direction, symbol, and entry;
- direction, symbol, and SL;
- direction, symbol, and TP;
- direction, symbol, SL, and TP.

The route assembly deadline begins with the first opening message and is not shortened by later updates. A later message enriches the existing assembly but does not reset the safety window indefinitely.

When the fields become complete, execution begins immediately. When the deadline passes first, the generation expires and produces a visible activity event.

Unsafe minimum-field choices remain available only after explicit warning confirmation.

## AI Interpretation Policy

The AI parser returns structured fields, action type, references, and confidence. The execution decision is deterministic code.

Confidence is recorded for audit and user guidance but does not reject a signal by itself. A signal is blocked only when:

- required fields are missing;
- the instruction is ambiguous;
- the action is disabled by the route;
- the signal is stale for an immediate market entry;
- symbol resolution fails;
- account or exposure safety fails;
- the parser output violates the schema.

Parser calls use a strict timeout and bounded retry. Exhaustion creates a retryable event and visible activity. It never acknowledges the Telegram message as successfully processed.

The system records parser model, parser version, request latency, structured result, decision reason, and correlation ID. Raw source text remains encrypted at rest.

## Broker Execution And State

Every broker action has:

- a unique database intent;
- a stable idempotency key;
- a stable MT5 client order identifier;
- one target account lock;
- one expected broker-state transition.

Unknown submission outcomes are reconciled before retry. Opening orders are never blindly resubmitted after a timeout.

Broker confirmation updates:

- intent state;
- route assembly generation state;
- copied-trade broker identifiers;
- position or order lifecycle;
- conversation opening status;
- user activity and notification state.

Permanent failures terminate the generation and leave the route active unless the account itself becomes unavailable.

## Symbol Resolution And Account Safety

The execution worker uses the account's complete MT5 symbol catalog, not only Market Watch visibility. Resolution filters out non-tradable symbols and ranks normalized matches by exactness, contract size, spread, and visibility.

Before every broker submission, the system validates:

- verified trader credentials;
- route, account, and global pause state;
- broker trading permission;
- symbol tradability;
- broker volume minimum, maximum, and step;
- fixed-lot and TP distribution;
- aggregate copied exposure;
- configured account maximum;
- available broker preflight evidence where supported.

Accounts display `Import only` or `Full access`. Only full-access accounts may activate copy rules.

## Runtime Reliability

Redis stream workers preserve source ordering and account serialization. Processing uses explicit `success`, `retry`, and `dead_letter` outcomes.

- Transient parser, Redis, database, MT5, and Telegram failures are retried with bounded backoff.
- Permanent invalid actions are durably recorded before acknowledgement.
- Retry exhaustion creates a PostgreSQL dead letter.
- Dead letters are visible and replayable.
- Worker restart recovery republishes unresolved intents and reconciles uncertain broker outcomes.
- Stream retention is bounded.

## User Experience

The UI presents:

- setup readiness;
- active and paused copy rules;
- Telegram connection state;
- full-access account state;
- signal stages;
- required-detail waiting and expiry;
- parser or broker failures;
- uncertain broker confirmation;
- successful execution and management actions.

Confidence may be shown as advisory context but never as channel support or eligibility. The UI contains no learning, analyze-again, supported, unsupported, or channel-confidence workflow.

Errors appear both inline where corrective input is required and as toasts for immediate feedback.

## Observability And Alerts

Required production evidence includes:

- fresh heartbeats for Telegram, signal, and execution workers;
- Redis stream lag and pending counts;
- parser success rate, timeout rate, and latency percentiles;
- signal assembly duration and expiry rate;
- broker submission and reconciliation latency;
- uncertain intent count and age;
- dead-letter count and age;
- successful and failed broker actions by route and account;
- MT5 session and trading-permission health.

Alerts fire for:

- missing or stale worker heartbeat;
- increasing stream lag;
- parser failure-rate breach;
- dead-letter creation;
- uncertain intent older than two reconciliation cycles;
- repeated Telegram reauthentication;
- repeated MT5 authorization or broker submission failure;
- database or Redis unavailability.

## Guarded Launch Controls

The initial production launch requires:

1. A separate Railway production environment and production secrets.
2. A global server-side kill switch.
3. Per-account pause controls.
4. Per-route pause controls.
5. Conservative default maximum copied exposure.
6. Emergency close and pending-order cancellation controls.
7. A dedicated synthetic Telegram source and broker test account.
8. A documented rollback order.
9. An on-call owner able to pause automation immediately.

The system must fail closed: if readiness cannot be established, no new broker action is accepted. Existing broker positions are not automatically closed merely because a dependency becomes unhealthy.

## Test And Release Gates

### Automated

Backend tests cover:

- split opening messages;
- SL and TP arriving later;
- two simultaneous symbols;
- reply-aware matching;
- ambiguous updates;
- edited messages before and after submission;
- confidence as advisory;
- assembly expiry;
- exact-once intent creation;
- timeout reconciliation;
- worker restart recovery;
- account and exposure limits;
- generation terminal states.

MT5 tests cover:

- stable client identifiers;
- duplicate request replay;
- market and pending orders;
- modification, break-even, partial close, full close, and cancellation;
- broker timeout after acceptance;
- authorization and trading-permission failure;
- symbol and volume validation.

Frontend tests cover:

- no historical-learning UI;
- setup and trader-access states;
- waiting, expiry, ambiguity, uncertain, success, and failure wording;
- operational health;
- kill-switch and pause controls;
- responsive and accessible dialogs.

### Integrated

A deterministic end-to-end harness must prove:

1. `BUY XAUUSD` waits when SL is required.
2. `SL 2315` completes the same opening generation.
3. Exactly one broker order is created.
4. A later `TP 2340` updates or extends the correct copied trade according to route settings.
5. A timeout after broker acceptance still produces one order.
6. A worker restart resumes unresolved work without duplication.
7. Two simultaneous symbols remain isolated.

### Production Acceptance

Before enabling live users:

- migrations are at head;
- all required services report ready;
- no unresolved old uncertain intents exist;
- no pending dead letters exist;
- stream lag is stable;
- synthetic market and management actions complete;
- global pause and rollback are exercised;
- logs and alerts are visible to the operator;
- legal, privacy, and terms review is recorded separately.

## Deployment Strategy

1. Deploy additive database changes.
2. Deploy MT5 idempotency and reconciliation.
3. Deploy backend API readers compatible with old and new records.
4. Pause automation.
5. Deploy Telegram, signal, and execution workers.
6. Run migrations and health checks.
7. Deploy the frontend.
8. Run the synthetic end-to-end test.
9. Exercise global pause and resume.
10. Enable guarded live access.

Rollback pauses automation first, then rolls back frontend, signal worker, execution worker, Telegram worker, API, and MT5 in that order. Additive database state remains in place.

## Superseded Requirements

This design supersedes historical-learning and unsupported-channel sections in earlier Copy Trading specifications and operations documents. Channel history may be retained only as optional internal research data and cannot influence channel eligibility or runtime execution.

## Non-Code Launch Dependency

Engineering can establish technical readiness, but it cannot declare regulatory authorization. Legal counsel must review applicable investment, automated-trading, privacy, Telegram, broker, and AI-provider obligations before unrestricted public marketing.

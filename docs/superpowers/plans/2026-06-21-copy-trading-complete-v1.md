# Copy Trading Complete V1 Implementation Plan

> Execute this plan continuously across `synctrades-be`, `mt5-quant-server`, `synctrades-fe`, and Railway dev. The approved design in `docs/superpowers/specs/2026-06-19-telegram-copy-trading-design.md` is the source of truth.

## Goal

Ship the complete Telegram-to-MT5 v1: Telegram user authentication and source discovery, reusable channel learning, low-latency AI action assembly, durable idempotent execution, complete MT5 trade management, activity/notification/safety controls, and the corresponding user interface and Railway services.

## Architecture Contract

- PostgreSQL is authoritative for profiles, samples, signal state, intents, copied trades, mappings, and audit events.
- Dedicated Redis streams carry Telegram commands/events, parsing work, and execution work. Celery is not on the trading hot path.
- Every broker request follows `normalized event -> parsed action -> deterministic validation -> durable intent -> broker call -> durable result`.
- Actions for one MT5 account are serialized; different accounts execute concurrently.
- Unknown broker outcomes enter reconciliation and may retry only after absence is proven.
- Email, activity enrichment, learning, journal sync, and analytics never delay broker submission.

## Task 1: Complete Domain Schema And Contracts

Add channel profiles and encrypted seven-day samples; signal threads and immutable action revisions; durable intents and copied trades; symbol mappings; Telegram auth/connection state; route controls, author policy, minimum fields, management permissions, notifications, and emergency audit data. Add one Alembic migration after the existing copy-trading head. Add focused model/schema tests first, then run migration upgrade/downgrade tests.

## Task 2: Dedicated Stream Runtime

Add a typed Redis stream bus, consumer-group bootstrap, idempotent acknowledgement, delayed retry scheduling, correlation IDs, latency metrics, and process entry points for `telegram-session`, `copy-signal`, `copy-execution`, and `copy-learning`. Add health heartbeats and graceful shutdown. Unit-test serialization, redelivery, dedupe, and handler dispatch before implementation.

## Task 3: Telegram Authentication And Ingestion

Add Telethon. Implement phone/code/2FA and QR auth APIs using short-lived Redis challenges; never persist the 2FA password. Encrypt StringSession values at rest. Add connection listing, revoke, reauthenticate, dialog discovery, source selection, and source state APIs. The session worker restores authorized sessions, filters source/author policy, handles new/edit/delete events, and publishes normalized events. Tests cover auth state transitions, lost authorization, forwards, replies, edits, deletes, and author filtering.

## Task 4: Channel Learning

Implement seven-day history scans, encrypted sample retention, image-dependence detection, profile reuse with quick revalidation, and structured AI learning output. Persist signal style, learned assembly minimum, image frequency, supported language/actions, confidence, parser version, and validation time. Medium/high confidence may proceed; low confidence and image-primary sources auto-disable. Tests use fixture channels and deterministic model responses.

## Task 5: Signal Engine

Implement strict structured AI parsing for complete and fragmented messages. Match threads by reply, explicit reference, symbol/direction, then recency. Reparse edits, expire incomplete setups, alert on post-execution deletes, and apply strict message/route/action dedupe. Deterministic validation enforces confidence, freshness, minimum fields, entry zones, pending-order policy, route permissions, lot policy, TP strategy, and all pause scopes. Persist an intent before publishing execution. Tests cover every parser and validation case in the approved design.

## Task 6: MT5 Execution Surface

Extend `mt5-quant-server` with the full `symbols_get()` catalog (including invisible symbols), symbol selection, market and pending order placement, pending cancellation, SL/TP modification, break-even, partial/full close, and reconciliation lookup by magic/comment/order/deal/position. Validate lot min/max/step without rounding. Add API and worker contract tests first.

## Task 7: Execution Coordinator And Reconciliation

Implement per-account Redis locking and ordered consumption, symbol normalization/mapping with contract-size tie-breaking, TP leg planning, magic/comment tagging, broker error classification, uncertain-state reconciliation, restart reconciliation, and independent multi-account fanout. Persist copied broker identifiers and publish outcome events. Tests cover duplicate delivery, crashes, timeouts, stale locks, broker acceptance without response, and partial fanout failures.

## Task 8: Safety, Activity, And Notifications

Implement global/connection/source/route/account pause enforcement and emergency close/cancel/both at global/account/source/route scope with confirmation tokens. Add grouped and chronological activity queries, raw-message owner-only reveal, permanent action details, and email for every successful broker action and every failure. User-facing messages must describe actions, never queues/workers/internal exceptions.

## Task 9: Complete Frontend

Build the Telegram connection wizard (QR and phone/code/2FA), connection/reauth states, dialog/source picker, learning progress and learned profile summary, route wizard, all fixed-lot/TP/minimum-field/management/author/pending/notification settings, account and symbol overrides, route status management, activity timeline and filters, debug reveal, pause controls, emergency actions, and immediate toast feedback. Preserve the existing TradePartna design system and responsive behavior. Add contract/component tests and verify rendered desktop/mobile flows.

## Task 10: Verification And Railway Delivery

Run backend migration and focused/full tests, MT5 tests, frontend lint/tests/build, and API contract smoke tests. Commit and push each repository. In Railway dev, create `telegram-session-worker`, `copy-signal-worker`, `copy-execution-worker`, and `copy-learning-worker` from the backend repository/image. Configure shared database, Redis, Telegram, encryption, AI, MT5, email, and URL variables by reference; set only the role-specific `PROCESS_TYPE` per service. Deploy, verify migrations, worker heartbeats, public API/UI, and an end-to-end mocked execution path. Do not enable broad production rollout flags.

## Completion Checklist

- All approved v1 behavior has an implementation and automated test.
- No broker action bypasses deterministic validation or durable intent persistence.
- No duplicate broker action is possible from redelivery, edits, retries, or restarts.
- Telegram 2FA passwords and raw samples are handled according to the privacy contract.
- Full symbol catalog and all supported management actions work through MT5.
- UI exposes every user decision and operational state without infrastructure language.
- Four dedicated Railway services are deployed and healthy in dev.
- Fresh verification evidence and deployed smoke-test results are recorded before completion.

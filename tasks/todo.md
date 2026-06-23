# Copy Trading Launch Audit

- [x] Establish focused backend and MT5 test baselines.
- [x] Verify both supplied accounts are demo, authenticated, and trade-enabled.
- [x] Add a broad signal-format corpus covering market, pending, updates, closes, edits, replies, noise, and malformed inputs.
- [x] Add sequence/property tests for split signals, interleaving symbols, duplicates, retries, ambiguity, and expiry.
- [x] Measure parser latency and optimize the verified critical-path bottleneck.
- [x] Fix every reproduced correctness or recovery defect with red-green tests.
- [x] Run full backend, MT5, and frontend verification.
- [x] Deploy affected Railway services and run a safe end-to-end demo acceptance test.
- [x] Record launch evidence, remaining blockers, and rollback steps.
- [ ] Remove the obsolete Railway `copy-learning-worker` service after Railway destructive actions are re-authenticated.

## Review

Local verification completed for the copy-trading parser, workers, MT5 client, MT5 worker, and frontend. Both supplied demo accounts were verified against the public dev MT5 API as authenticated, trade-enabled, and demo-only. Safe broker acceptance passed with min-lot EURUSD orders: first submit created one position, replay returned idempotent, cleanup closed the remaining position.

Latest backend MT5 client hardening keeps a bounded HTTP keep-alive pool for POST/GET job polling while preserving transient retry handling for stale server disconnects.

Remaining launch blockers:
- Active dev copy-trading services are online (`tradepartna-api`, `copy-signal-worker`, `copy-execution-worker`, `telegram-session-worker`), but the obsolete `copy-learning-worker` service still shows crashed. Deleting it is blocked by Railway with `Unauthorized. Please run railway login again.`
- The local full backend suite requires a running Postgres test database at `localhost:5432/test` with `user/pass`; without that DB, 12 DB-backed journal tests fail at connection time. Non-DB backend tests reached 366 passed before those DB-only failures.
- Rotate exposed demo/broker/API credentials and internal shared secrets before real production launch.

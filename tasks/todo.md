# Copy Trading Launch Audit

- [x] Establish focused backend and MT5 test baselines.
- [ ] Verify both supplied accounts are demo, authenticated, and trade-enabled.
- [x] Add a broad signal-format corpus covering market, pending, updates, closes, edits, replies, noise, and malformed inputs.
- [x] Add sequence/property tests for split signals, interleaving symbols, duplicates, retries, ambiguity, and expiry.
- [x] Measure parser latency and optimize the verified critical-path bottleneck.
- [x] Fix every reproduced correctness or recovery defect with red-green tests.
- [x] Run full backend, MT5, and frontend verification.
- [ ] Deploy affected Railway services and run a safe end-to-end demo acceptance test.
- [ ] Record launch evidence, remaining blockers, and rollback steps.

## Review

Local verification completed. Railway authentication is expired, so account capability checks, live logs, deployment, and safe broker acceptance remain pending.

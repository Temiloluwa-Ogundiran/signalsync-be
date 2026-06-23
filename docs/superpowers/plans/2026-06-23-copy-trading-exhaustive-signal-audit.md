# Copy Trading Exhaustive Signal Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove and harden copy trading across realistic Telegram signal formats and sequences while keeping broker testing safe and latency measurable.

**Architecture:** Use a deterministic fast-path parser for unambiguous text and the existing structured AI parser as fallback. Exercise production assembly and execution contracts with table-driven and sequence tests, then validate only demo, trade-enabled accounts against Railway while the global kill switch remains available.

**Tech Stack:** FastAPI, SQLAlchemy, Redis Streams, OpenAI structured output, pytest, Hypothesis-style generated permutations, MT5 Python API, Next.js, Railway.

---

### Task 1: Account And Runtime Safety Gate

**Files:**
- Modify: `tasks/todo.md`
- Inspect: `scripts/copy_trading_launch_check.py`
- Inspect: `app/mt5_worker/handlers/accounts.py` in `mt5-quant-server`

- [ ] Verify Railway authentication and global pause.
- [ ] Verify each supplied account without submitting an order.
- [ ] Record demo/live status, `trade_allowed`, server match, and read-only status.
- [ ] Permit broker acceptance testing only for accounts proven to be demo and trade-enabled.

### Task 2: Signal Corpus And Parser Contract

**Files:**
- Create: `tests/fixtures/copy_signal_corpus.py`
- Create: `src/app/domains/copy_trading/parser.py`
- Modify: `src/app/domains/copy_trading/workers.py`
- Create: `tests/test_copy_trading_parser_corpus.py`

- [ ] Write failing table-driven tests for compact, verbose, multiline, emoji, punctuation, decimal, range, pending-order, management, forwarded, reply, typo-tolerant, and irrelevant messages.
- [ ] Run the corpus tests and confirm failures reflect missing deterministic parsing behavior.
- [ ] Implement only unambiguous deterministic patterns; retain AI fallback for ambiguous natural language.
- [ ] Verify exact prices are preserved and missing fields are never invented.
- [ ] Benchmark deterministic parsing and require p95 below 10 ms locally.

### Task 3: Sequence And State-Machine Coverage

**Files:**
- Modify: `tests/test_copy_trading_assembly.py`
- Modify: `tests/test_copy_trading_end_to_end.py`
- Modify: `tests/test_copy_trading_workers.py`
- Modify as failures require: `src/app/domains/copy_trading/assembly.py`
- Modify as failures require: `src/app/domains/copy_trading/generations.py`
- Modify as failures require: `src/app/domains/copy_trading/workers.py`

- [ ] Add permutations for open-SL-TP in every ordering.
- [ ] Add interleaved two-symbol, reply-thread, edited-message, forwarded-message, duplicate-delivery, out-of-order, stale, ambiguous, and expiry cases.
- [ ] Add action chains for modify SL/TP, break even, additional TP, partial close, full close, and pending cancellation.
- [ ] Confirm each reproduced failure before applying one minimal fix.
- [ ] Verify exactly-once intent creation and no cross-symbol context leakage.

### Task 4: Execution And Recovery Coverage

**Files:**
- Modify: `tests/test_copy_trading_execution_safety.py`
- Modify: `tests/test_copy_trading_reconciliation.py`
- Modify MT5 tests under `mt5-quant-server/app/tests/`

- [ ] Cover broker reject, timeout before submission, timeout after acceptance, restart, retry, reconciliation, market closure, invalid symbol, invalid volume, insufficient margin, and read-only account.
- [ ] Verify stable client-order identifiers and no duplicate order after uncertain outcomes.
- [ ] Verify account and route maximum-lot policies for multiple TP modes.

### Task 5: Latency And Operational Readiness

**Files:**
- Create: `scripts/benchmark_copy_signal_processing.py`
- Modify: `src/app/domains/copy_trading/worker_runtime.py` only when measurements justify it.
- Modify: `docs/copy-trading-operations.md`

- [ ] Benchmark deterministic parse, assembly, Redis publication, and MT5 job submission separately.
- [ ] Remove avoidable synchronous work from the message-to-intent critical path.
- [ ] Run the launch checker and verify migrations, worker health, dead letters, active intents, uncertain intents, and synthetic correlation evidence.

### Task 6: Full Verification And Safe Deployment

**Files:**
- Update: `tasks/todo.md`
- Update: `docs/copy-trading-operations.md`

- [ ] Run the complete backend suite, lint, and migration checks.
- [ ] Run the complete MT5 suite.
- [ ] Run frontend copy-trading tests, lint, and production build.
- [ ] Deploy changed services in dependency order.
- [ ] Run one minimal demo broker acceptance per trade-enabled demo account, then close/cancel created test objects.
- [ ] Re-run launch readiness and document evidence and remaining blockers.

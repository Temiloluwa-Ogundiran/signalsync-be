# MT5 Verification Timing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Observe valid MT5 verification results within five seconds after queue admission without exposing temporary queue pressure to users.

**Architecture:** The backend separates admission waiting from processing timeout. Verification jobs retry 429/503 admission responses using a normal retry cadence, then poll an admitted job every 100 milliseconds for at most four seconds. The frontend leaves account connection uncapped so the explicit busy-queue exception can wait, while the worker uses its existing one-attempt three-second verification mode.

**Tech Stack:** FastAPI, AnyIO, HTTPX, pytest, Next.js, Axios, Python MT5 worker

---

### Task 1: Backend timing contract

**Files:**
- Modify: `src/app/core/config.py`
- Modify: `docker-compose.yml`
- Modify: `src/app/domains/accounts/mt5_core_client.py`
- Modify: `src/app/domains/accounts/service.py`
- Test: `tests/test_mt5_core_client.py`
- Test: `tests/test_account_connect_mt5.py`

- [ ] **Step 1: Write failing regression tests**

Add a client test that receives two 429 responses over a duration longer than
`poll_timeout`, is then admitted, and succeeds. Update the account service test
to require construction with both timing parameters:

```python
mock_client_cls.assert_called_once_with(
    poll_timeout=4,
    poll_interval=0.1,
)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='src'; pytest tests\test_mt5_core_client.py tests\test_account_connect_mt5.py -q
```

Expected: admission test raises `Mt5CoreClientTimeout`, and constructor assertion
reports the missing `poll_interval` argument.

- [ ] **Step 3: Implement separate timing phases**

Add:

```python
MT5_CORE_FAST_VERIFY_POLL_INTERVAL_SECONDS: float = 0.1
```

Pass it to `Mt5CoreClient` from `connect_account`. Remove the outer
`anyio.fail_after` because `_poll_job` already starts its timeout after job
admission. Give `Mt5CoreClient` a separate admission retry interval that
defaults to the normal two-second setting, and let 429/503 submission retries
continue until admission.

- [ ] **Step 4: Run tests and verify GREEN**

Run the focused command from Step 2. Expected: all tests pass.

- [ ] **Step 5: Commit backend implementation**

```powershell
git add docker-compose.yml src/app/core/config.py src/app/domains/accounts/mt5_core_client.py src/app/domains/accounts/service.py tests/test_mt5_core_client.py tests/test_account_connect_mt5.py
git commit -m "Fix MT5 verification timing race"
```

### Task 2: Frontend busy-wait behavior

**Files:**
- Modify: `src/features/journal/api/journal-account.api.ts`

- [ ] **Step 1: Remove the premature browser deadline**

Set the account connection request override to Axios unlimited mode:

```typescript
timeout: 0,
```

This preserves the backend's definitive result and allows queue admission to
wait without presenting a busy error.

- [ ] **Step 2: Verify frontend**

Run:

```powershell
npm run lint
npm run build
```

Expected: zero lint errors and a successful production build.

- [ ] **Step 3: Commit frontend implementation**

```powershell
git add src/features/journal/api/journal-account.api.ts
git commit -m "Let MT5 connection wait for admission"
```

### Task 3: Worker promotion and end-to-end verification

**Files:**
- Existing worker changes: `app/mt5_worker/main.py`
- Existing worker changes: `app/mt5_worker/session.py`
- Existing tests: `app/tests/test_worker_runtime.py`
- Existing tests: `app/tests/test_worker_session.py`

- [ ] **Step 1: Verify worker branch**

Run:

```powershell
pytest app\tests -q
```

Expected: 99 tests pass.

- [ ] **Step 2: Replay the timing reproduction**

Run the HTTPX timing harness with a job that succeeds at three seconds, a
four-second processing timeout, and a 100ms poll interval. Expected: verified
result before four seconds, not `TIMEOUT`.

- [ ] **Step 3: Push backend and frontend**

Rebase each `staging` branch onto `origin/staging`, rerun affected checks, and
push normally.

- [ ] **Step 4: Merge worker PR and verify remote state**

Mark PR #1 ready, merge it into `main`, update the local checkout, and verify
that local and remote hashes match. Confirm all three worktrees are clean.

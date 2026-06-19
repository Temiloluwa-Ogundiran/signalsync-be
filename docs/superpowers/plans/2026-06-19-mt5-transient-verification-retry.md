# MT5 Transient Verification Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically retry one failed MT5 verification job when and only when the failure is an IPC/transport error.

**Architecture:** `Mt5CoreClient` classifies terminal job errors and owns the one-retry loop because a fresh job receives an independent queue and processing budget. Account service keeps definitive credential failures separate and maps exhausted transient failures to HTTP 503 `Service Error`.

**Tech Stack:** Python, FastAPI, HTTPX, AnyIO, pytest

---

### Task 1: Classify and retry transient MT5 jobs

**Files:**
- Modify: `src/app/domains/accounts/mt5_core_client.py`
- Test: `tests/test_mt5_core_client.py`

- [ ] **Step 1: Write failing client tests**

Add three tests:

```python
async def test_verify_credentials_retries_one_ipc_failure_then_succeeds(): ...
async def test_verify_credentials_stops_after_two_ipc_failures(): ...
async def test_verify_credentials_does_not_retry_authorization_failure(): ...
```

The first registers two POST jobs, an IPC `-10005` failure, and a success. The
second registers exactly two IPC failures and expects
`Mt5CoreClientTransientJobFailed`. The third registers MT5 code `-6`, expects
`Mt5CoreClientJobFailed`, and asserts only one POST request.

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; pytest tests\test_mt5_core_client.py -q
```

Expected: first test raises on the first job and the transient exception does
not exist.

- [ ] **Step 3: Implement classification and one retry**

Add `Mt5CoreClientTransientJobFailed` and a private classifier recognizing
codes `-10005`, `-10004`, `-10003` plus IPC, pipe, and connection wording.
Extract one submit-and-poll operation, then call it at most twice from
`verify_credentials`, retrying only the transient exception.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: all client tests pass.

### Task 2: Map exhausted transport failures correctly

**Files:**
- Modify: `src/app/domains/accounts/service.py`
- Test: `tests/test_account_connect_mt5.py`

- [ ] **Step 1: Write failing service test**

Make `verify_credentials` raise `Mt5CoreClientTransientJobFailed` and assert:

```python
assert exc.value.status_code == 503
assert exc.value.detail == "Service Error"
```

- [ ] **Step 2: Run test and verify RED**

```powershell
$env:PYTHONPATH='src'; pytest tests\test_account_connect_mt5.py -q
```

Expected: the generic MT5 service error text is returned instead.

- [ ] **Step 3: Add explicit service mapping**

Catch `Mt5CoreClientTransientJobFailed` before the base client exception and
return HTTP 503 with exact detail `Service Error`.

- [ ] **Step 4: Run focused verification**

```powershell
$env:PYTHONPATH='src'; pytest tests\test_mt5_core_client.py tests\test_account_connect_mt5.py -q
```

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/app/domains/accounts/mt5_core_client.py src/app/domains/accounts/service.py tests/test_mt5_core_client.py tests/test_account_connect_mt5.py
git commit -m "Retry transient MT5 verification once"
```

### Task 3: Deploy and verify production

**Files:**
- No additional files

- [ ] **Step 1: Replay the production failure sequence locally**

Use an HTTPX harness that returns IPC `-10005` for job one and success for job
two. Expected: one verified result and exactly two submissions.

- [ ] **Step 2: Rebase and rerun focused tests**

Rebase `staging` onto `origin/staging`, then rerun the focused suite.

- [ ] **Step 3: Push and wait for Railway**

Push `staging`, identify the matching GitHub deployment, and wait for Railway
success.

- [ ] **Step 4: Verify production health and logs**

Confirm backend health is 200, remote SHA matches, and new connection requests
no longer label IPC `-10005` as authorization failures.

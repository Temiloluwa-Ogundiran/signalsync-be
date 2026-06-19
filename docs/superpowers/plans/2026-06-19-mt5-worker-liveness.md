# MT5 Worker Liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let healthy queued verification jobs wait while returning `Service Error` when the MT5 worker heartbeat is stale.

**Architecture:** The shared Redis store owns a five-second worker heartbeat. A background publisher remains active while the worker processes jobs, and the MT5 API exposes liveness on job status responses. The worker persists `running` to Postgres immediately, allowing the backend to start its four-second deadline only for active processing.

**Tech Stack:** Python, FastAPI, Redis, Postgres, threading, HTTPX, AnyIO, pytest, unittest

---

### Task 1: Shared heartbeat storage

**Files:**
- Modify: `mt5-quant-server/app/shared/store.py`
- Modify: `mt5-quant-server/app/fastapi_app/adapters/redis_client.py`
- Test: `mt5-quant-server/app/tests/test_job_store.py`

- [ ] **Step 1: Write failing heartbeat tests**

Add tests proving a new in-memory store reports unavailable, reports available
after publication, and reports unavailable after its expiry time is advanced.
Also assert that `RedisClient` delegates heartbeat publication and reads.

```python
store.publish_worker_heartbeat(ttl_seconds=5)
self.assertTrue(store.is_worker_available())
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
pytest app\tests\test_job_store.py -q
```

Expected: failures because the heartbeat methods do not exist.

- [ ] **Step 3: Implement heartbeat storage**

Add `publish_worker_heartbeat(ttl_seconds=5)` and `is_worker_available()` to
`InMemoryStore`, `RedisStore`, and `RedisClient`. Redis uses the fixed key
`workers:mt5:heartbeat` with expiry; memory stores an expiration timestamp.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

### Task 2: Worker heartbeat and durable running state

**Files:**
- Create: `mt5-quant-server/app/mt5_worker/heartbeat.py`
- Modify: `mt5-quant-server/app/mt5_worker/main.py`
- Test: `mt5-quant-server/app/tests/test_worker_heartbeat.py`
- Test: `mt5-quant-server/app/tests/test_worker_runtime.py`

- [ ] **Step 1: Write failing lifecycle tests**

Test that `WorkerHeartbeat.start()` publishes immediately and its daemon loop
continues independently. Update runtime success coverage to require two durable
writes in this order:

```python
self.assertEqual(db.upsert_job_record.call_args_list[0].args[0]["status"], "running")
self.assertEqual(db.upsert_job_record.call_args_list[1].args[0]["status"], "succeeded")
```

- [ ] **Step 2: Run tests and verify RED**

```powershell
pytest app\tests\test_worker_heartbeat.py app\tests\test_worker_runtime.py -q
```

Expected: heartbeat import fails and runtime writes only the terminal status.

- [ ] **Step 3: Implement lifecycle publication**

Create `WorkerHeartbeat` with a daemon thread, one-second interval, five-second
TTL, and idempotent `start`/`stop`. `WorkerRuntime.run_forever()` starts it and
stops it in `finally`. After the worker writes hot `running`, immediately pass
that full record to `write_durable_status` before MT5 connection work begins.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

- [ ] **Step 5: Commit worker lifecycle changes**

```powershell
git add app/shared/store.py app/fastapi_app/adapters/redis_client.py app/mt5_worker/heartbeat.py app/mt5_worker/main.py app/tests/test_job_store.py app/tests/test_worker_heartbeat.py app/tests/test_worker_runtime.py
git commit -m "Expose MT5 worker liveness and running state"
```

### Task 3: MT5 API liveness status

**Files:**
- Modify: `mt5-quant-server/app/fastapi_app/services/job_service.py`
- Test: `mt5-quant-server/app/tests/test_job_service.py`

- [ ] **Step 1: Write failing status tests**

For both healthy and stale worker mocks, assert `get_job()` includes:

```python
{"worker_available": True}
```

or `False` while preserving the job status.

- [ ] **Step 2: Run tests and verify RED**

```powershell
pytest app\tests\test_job_service.py -q
```

Expected: `worker_available` is absent.

- [ ] **Step 3: Add liveness to status responses**

Set `sanitized["worker_available"] = self._client.is_worker_available()` in
`JobService.get_job()` after sanitizing the durable record.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

- [ ] **Step 5: Commit MT5 API status changes**

```powershell
git add app/fastapi_app/services/job_service.py app/tests/test_job_service.py
git commit -m "Report MT5 worker availability on jobs"
```

### Task 4: Backend state-aware polling

**Files:**
- Modify: `synctrades-be/src/app/domains/accounts/mt5_core_client.py`
- Modify: `synctrades-be/src/app/domains/accounts/service.py`
- Test: `synctrades-be/tests/test_mt5_core_client.py`
- Test: `synctrades-be/tests/test_account_connect_mt5.py`

- [ ] **Step 1: Write failing polling tests**

Add a queued-healthy test that remains queued longer than `poll_timeout`, then
runs and succeeds within the processing window. Add a queued-stale test that
raises `Mt5CoreClientWorkerUnavailable`. Add a service test that maps that
exception to HTTP 503 detail `Service Error`.

- [ ] **Step 2: Run tests and verify RED**

```powershell
$env:PYTHONPATH='src'; pytest tests\test_mt5_core_client.py tests\test_account_connect_mt5.py -q
```

Expected: healthy queued job times out and unavailable exception is undefined.

- [ ] **Step 3: Implement state-aware deadline**

Add `Mt5CoreClientWorkerUnavailable`. In `_poll_job`, leave the processing start
unset while status is `queued`; if `worker_available is False`, raise the new
exception. Set the processing start on first `running`, and enforce
`poll_timeout` only from that timestamp. Map the new exception to HTTP 503 with
exact detail `Service Error`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

- [ ] **Step 5: Commit backend polling changes**

```powershell
git add src/app/domains/accounts/mt5_core_client.py src/app/domains/accounts/service.py tests/test_mt5_core_client.py tests/test_account_connect_mt5.py
git commit -m "Distinguish busy and unavailable MT5 workers"
```

### Task 5: Full verification and deployment

**Files:**
- No additional files

- [ ] **Step 1: Run complete worker verification**

```powershell
pytest app\tests -q
```

Expected: all worker tests pass.

- [ ] **Step 2: Run backend verification and queued-state reproduction**

Run the focused backend suite and replay a delayed queued job. Expected: healthy
queue delay succeeds; stale worker produces `Service Error`.

- [ ] **Step 3: Publish in dependency order**

Push and deploy `mt5-quant-server/main` first. After worker and MT5 API are live,
push `synctrades-be/staging` so the backend never expects a liveness field from
an older MT5 API during rollout.

- [ ] **Step 4: Verify remote hashes and production health**

Confirm local and remote hashes match, all worktrees are clean, the public API
health endpoint no longer returns Cloudflare 522, and the MT5 worker heartbeat
is visible through a queued job status response.

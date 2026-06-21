# synctrades-be — Engineering Rules

**Read this before writing or changing ANY code in this repo.** These rules are mandatory.
Every rule traces to a real bug or scaling hazard we audited (see `../CODE_AUDIT_REPORT.md`).
If a rule blocks you, stop and ask — do not improvise around it.

**If you are an LLM:** treat each rule as a hard constraint, like a failing test. If your
change would violate one, the change is wrong — not the rule. Never "temporarily" break a
rule with a TODO.

---

## 1. Layering — who is allowed to do what

```
router (HTTP)  →  service (logic)  →  repository (SQL)  →  models
        \________ schemas (pydantic) ________/
core/ + shared/ = framework-free utilities; they import NOTHING from app/domains/*
```

| Layer | Allowed | Forbidden |
|-------|---------|-----------|
| `domains/*/router.py` | parse request, call ONE service function, return schema | business logic, SQL, loops over DB calls |
| `domains/*/service*.py` | business rules, authorization, orchestration, `HTTPException`, `db.commit()` at PUBLIC entry points only | raw SQL strings (use repo), HTTP parsing |
| `domains/*/repository.py` | queries + persistence, `db.flush()` | `db.commit()`, `HTTPException`, authorization checks, business recalculation |
| `core/`, `shared/` | settings, security primitives, generic utils | importing from `app.domains.*` (one documented legacy exception: `shared/activity.py`) |

- **Authorization lives in services.** A repository must never check `user_id` ownership —
  the service loads the resource scoped by user (`get_*_for_user`) or checks ownership itself.
- **Exceptions don't leak across layers.** Repos raise nothing but DB errors; services raise
  `HTTPException`; routers translate nothing (the service already did).

---

## 2. Transactions — one boundary, owned by the outermost call

- **Repositories `flush()`. Services `commit()` only at their public entry point. Internal
  helpers and ingestion functions NEVER commit.** A `db.commit()` inside a loop or inside a
  function that callers compose is an automatic rejection (it breaks atomicity and it breaks
  transaction-scoped advisory locks — see §3).
- One request/task = one transaction unless there is a written reason. Error paths roll back
  or commit failure-state explicitly — never leave the session dirty.
- Never call `db.refresh()` in a loop. Never re-query what you just flushed without reason.

---

## 3. Database access rules (PgBouncer-compatible, 50k-user scale)

We run (or will run) behind **PgBouncer in transaction pooling mode**. Consequences:

- ❌ **No session-scoped state, ever**: no `pg_advisory_lock`/`pg_try_advisory_lock`
  (session-scoped), no `SET` without `LOCAL`, no temp tables across transactions, no
  server-side prepared statement reliance.
  ✅ Use `pg_try_advisory_xact_lock` — it auto-releases at commit/rollback and is
  pooling-safe. (This is also why §2's "no mid-function commits" matters: a commit releases
  your xact lock.)
- Migrations connect DIRECT to Postgres (`DATABASE_URL_DIRECT`), never through PgBouncer.

Query discipline:

- ❌ **No per-row statements in a loop.** Ingesting/updating N rows = ONE batched statement
  (`INSERT ... ON CONFLICT DO UPDATE` with `.values(list_of_dicts)`, chunked ~500). The old
  per-deal upsert turned a 5k-deal sync into 10k statements — never again.
- ❌ **No N+1.** Serializing a list never queries per item: batch-fetch children with one
  `IN (...)` query and map in Python. Resolving "latest X on-or-before date" per day in a
  loop = fetch once + `bisect`. If your code calls a repo function inside a `for` over query
  results, it is wrong.
- ❌ **No loading IDs into Python just to feed them back into a DELETE/UPDATE.** Use one
  statement with `RETURNING` for whatever you needed (counts, affected dates).
- **Aggregates that only need a few columns select tuples, not ORM entities**
  (`select(Trade.net_profit, ...)`), and where possible push the aggregation into SQL
  (`GROUP BY` / window functions) instead of Python loops over full tables.
- **Every list endpoint is paginated.** `limit: int = Query(50, le=100)` + cursor keyed on
  `(sort_col, id)` — copy the existing pattern in `accounts/repository.py::list_trades_by_account`.
  An unbounded `SELECT *` list endpoint does not merge.
- **New query patterns ship with their index** in the same PR (Alembic migration), and any
  table expected to grow unboundedly (tokens, messages) ships with a purge/retention story.
- **Money is `Decimal` end-to-end.** Cast to `float` only at the response-schema boundary.
- Concurrent get-or-create = DB-level idempotency (`ON CONFLICT DO NOTHING` + select), never
  "check then insert".
- Raw SQL strings: parameterized always; never f-string interpolation of values; **never use
  PostgreSQL reserved words as aliases** (we shipped `JOIN tag_options to` — a runtime syntax
  error). Any raw SQL must be executed by at least one test against a real test database.

---

## 4. Middleware & request path

- **Middleware never blocks the response on background work.** `await anyio.to_thread.run_sync(...)`
  before returning still delays THIS response — fire-and-forget (`run_in_executor`) after an
  in-memory cheap-check gate instead.
- Middleware never opens a DB session on the hot path without an in-process throttle in front
  of it (every session = a pool checkout; pool size is finite and shared).
- Every authenticated request already decodes the JWT in `deps` — don't add more per-request
  crypto/DB work without measuring.
- Body size limits, GZip, and security headers are configured in `main.py` middleware —
  if you add a new route family with special needs (uploads), use the existing exclusion
  mechanisms; don't remove the global protections.

---

## 5. Security — non-negotiable

**Auth dependency (`shared/deps.py`):**
- `get_current_user` validates: token decodes, `typ == "access"`, user exists,
  `not user.is_deleted`, `user.is_email_verified`. Any new state flag that can invalidate a
  session gets added HERE, in one place.

**Tokens:**
- Raw token values are never stored — SHA-256 hash only. Refresh tokens are opaque UUIDs,
  single-use, rotated, with the documented 30s reuse-grace window (concurrency) and
  revoke-all on reuse outside it, on logout-all, and on password change.
- Access JWTs carry `{"sub", "exp", "typ": "access"}`. No PII in JWT claims.

**Anti-enumeration & timing:**
- Endpoints taking an email (login, forgot-password, resend-verification) return identical
  responses AND identical timing whether or not the account exists — verify against a dummy
  bcrypt hash when the user is missing. An early-return on "user not found" before the hash
  check is a timing oracle and will be rejected.

**Rate limiting:**
- The limiter uses **Redis storage** (`storage_uri`) — never the in-memory default (it's
  per-process and multiplies limits by worker count).
- Keying: per-user (`sub` claim) when authenticated; forwarded client IP otherwise. Remember
  ALL browser traffic arrives from the Next proxy's IP — raw `request.client.host` keying
  throttles the whole platform as one user.
- Every new auth or expensive endpoint gets an explicit `@limiter.limit(...)`; the global
  default is a safety net, not a design.

**Input validation:**
- Client-supplied JSON blobs are validated against a pydantic model with
  `model_config = ConfigDict(extra="forbid")` — never parsed-and-stored raw.
- ❌ **No mass assignment.** Never `setattr(model, k, v)` over a dumped request schema —
  iterate an explicit `ALLOWED_*_FIELDS` whitelist.
- File uploads: enforce max bytes (from settings, not literals) AND magic-byte sniffing via
  the shared helper in `shared/utils/uploads.py`. Trusting `Content-Type` or filename is banned.

**Output:**
- Response schemas expose what the client needs — internal operational fields
  (`consecutive_sync_failures`, sync outcomes, encrypted blobs, hashes) never appear in a
  response model. Adding a model field does NOT mean adding it to the response schema.
- Before removing/renaming any response field, grep the frontend for it.

**Secrets:**
- Broker credentials only via `shared/utils/encryption.py` (Fernet). Secrets never in logs,
  never in exception messages, never in job payloads that get persisted. Compare secrets with
  `hmac.compare_digest`, never `==`.

---

## 6. Sync & background work

- **Sync is on-demand only.** There is NO scheduled/periodic sync — this protects the external
  MT5 service. Do not add Celery beat schedules, cron syncs, or "auto-refresh" endpoints.
  The only entry point is `POST /accounts/{id}/sync` → admission checks → Celery task.
- Per-account mutual exclusion = the transaction-scoped advisory lock (§3). Do not add
  in-process locks/sets for cross-request coordination — module-level state cannot work
  across workers and WILL silently no-op (we shipped exactly that bug).
- Celery worker concurrency is the deliberate throttle toward the MT5 service — changing it
  is a capacity decision, not a tuning whim; document any change.
- Long-running external HTTP (polling): one `httpx.AsyncClient` per job
  (`limits=httpx.Limits(max_keepalive_connections=0)` if the peer mishandles keep-alive) —
  never a new client per poll iteration.
- Anything slower than ~1s of work does not run in a request handler — it goes to Celery and
  the endpoint returns a queued/status response.

---

## 7. Configuration & dependencies

- Every tunable (limits, TTLs, sizes, pool numbers) is a `Settings` field with a comment —
  no magic literals at call sites.
- Pool math is documented where it's configured: total Postgres connections =
  processes × (POOL_SIZE + MAX_OVERFLOW); keep it under PgBouncer/Postgres capacity.
- Dependency policy: auth/crypto libraries must be actively maintained — we use `bcrypt`
  directly (not passlib) and `PyJWT` (not python-jose). Do not reintroduce either; do not
  pin a dependency to dodge an incompatibility without a linked issue + removal plan.
- New Prometheus/metric labels must be bounded-cardinality (no user ids, account ids, or
  tokens as label values).
- Never commit: `.env`, `__pycache__/`, `celerybeat-schedule*`, `.pyc`.

---

## 8. Code hygiene

- Dead code is deleted, not kept "just in case" — unused functions (we carried two dead
  token helpers in `core/security.py` for months) get removed in the PR that obsoletes them.
- Duplicated logic across domains (e.g. snapshot lookups) lives in ONE repository; the other
  domain imports it. Before writing a query helper, grep for an existing one.
- Datetimes are timezone-aware UTC (`datetime.now(timezone.utc)`) everywhere; account-local
  conversions only via `shared/utils/timezone.py` helpers.
- Logging: structured, with ids (`account_id=%s`), via module `logger` — never `print`,
  never logging secrets or full payloads.

---

## 9. Testing requirements

- Every bug fix ships a test that **fails on the old code**.
- Every raw-SQL statement has at least one test that executes it against a test database
  (string-building bugs like reserved-word aliases only surface at execution).
- Sync/ingestion changes are diff-tested against a recorded deal fixture: identical
  `SyncResult` counts before/after a refactor.
- Endpoint changes assert the response schema (so accidental field leaks/breaks are caught).

---

## 10. PR checklist (LLMs: self-verify before declaring done)

- [ ] No `db.commit()` outside a service's public entry point; repos only flush.
- [ ] No queries inside loops; no N+1; new list endpoints paginated; new query patterns indexed.
- [ ] No session-scoped Postgres features (advisory session locks, SET without LOCAL).
- [ ] No mass assignment; client JSON validated with `extra="forbid"`; uploads size+magic-checked.
- [ ] No new response-schema fields exposing internals; FE grepped before removing fields.
- [ ] Rate limit declared for any new auth/expensive endpoint.
- [ ] No module-level mutable state used for cross-request coordination.
- [ ] Decimal preserved in money math; UTC-aware datetimes only.
- [ ] Tests included per §9; behavior unchanged unless the task explicitly says otherwise.

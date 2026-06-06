# Remove MetaAPI From Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove MetaAPI runtime support from the backend so MT5-core is the only broker-account sync path and `.env.example` only documents live backend settings.

**Architecture:** Delete the MetaAPI client and config surface, simplify sync and journal code to rely on MT5 snapshots only, and update account defaults to `headless_mt5`. Add a small data migration that rewrites any legacy `metaapi` rows to `headless_mt5` while leaving the Postgres enum type itself stable.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL enums, pytest

---

## File Map

- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-be/alembic/versions/<new_revision>_migrate_metaapi_accounts_to_headless_mt5.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/.env.example`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/core/config.py`
- Delete: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/metaapi.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/models.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/repository.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/service.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/sync.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/journal/service.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/journal/schemas.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/tasks/journal_sync_tasks.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/tests/test_mt5_sync_orchestrator.py`

### Task 1: Remove MetaAPI config and env surface

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/core/config.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/.env.example`

- [ ] **Step 1: Remove `METAAPI_*` settings from the backend config**

Delete the `METAAPI_*` fields from `Settings` in `src/app/core/config.py`, leaving `INITIAL_SYNC_LOOKBACK_DAYS` in place.

- [ ] **Step 2: Remove `METAAPI_*` variables from `.env.example`**

Delete the entire MetaAPI block from `.env.example`.

- [ ] **Step 3: Verify the env cleanup**

Run:

```powershell
rg -n "METAAPI_" C:\Users\USER\Documents\SyncTrade\synctrades-be\.env.example C:\Users\USER\Documents\SyncTrade\synctrades-be\src\app\core\config.py
```

Expected: no matches.

### Task 2: Remove MetaAPI runtime code and switch defaults to MT5

**Files:**
- Delete: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/metaapi.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/models.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/repository.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/service.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/accounts/sync.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/tasks/journal_sync_tasks.py`

- [ ] **Step 1: Make `headless_mt5` the default provider**

Update model and repository defaults from `SyncProvider.metaapi` to `SyncProvider.headless_mt5`.

- [ ] **Step 2: Remove MetaAPI imports and fallback error branches**

Replace MetaAPI-specific transient handling in `service.py` and `journal_sync_tasks.py` with generic sync error handling that fits the MT5-only backend.

- [ ] **Step 3: Simplify `sync_account_deals()` to MT5-only**

Remove the MetaAPI branch from `src/app/domains/accounts/sync.py` so `sync_account_deals()` always uses the MT5 path.

- [ ] **Step 4: Delete `metaapi.py`**

Only delete the file after all imports and usages are removed.

- [ ] **Step 5: Verify no runtime MetaAPI references remain**

Run:

```powershell
rg -n "metaapi|MetaAPI|METAAPI_" C:\Users\USER\Documents\SyncTrade\synctrades-be\src
```

Expected: no matches, or only intentional legacy-neutral comments that still accurately describe behavior.

### Task 3: Remove MetaAPI fallback from journal analytics

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/journal/service.py`
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/src/app/domains/journal/schemas.py`

- [ ] **Step 1: Remove MetaAPI account-info fallback from `_estimate_starting_balance()`**

Make `_estimate_starting_balance()` rely on persisted snapshots for broker-synced accounts and keep the CSV import special case.

- [ ] **Step 2: Update stale comments**

Replace comments/docstrings that describe “MetaAPI-sourced trades” or “MetaAPI fallback behavior” with MT5-only wording.

- [ ] **Step 3: Verify journal service is MetaAPI-free**

Run:

```powershell
rg -n "MetaAPI|metaapi" C:\Users\USER\Documents\SyncTrade\synctrades-be\src\app\domains\journal
```

Expected: no matches.

### Task 4: Migrate any legacy account rows

**Files:**
- Create: `C:/Users/USER/Documents/SyncTrade/synctrades-be/alembic/versions/<new_revision>_migrate_metaapi_accounts_to_headless_mt5.py`

- [ ] **Step 1: Add an Alembic migration that rewrites legacy rows**

Create a migration that runs:

```sql
UPDATE trading_accounts
SET sync_provider = 'headless_mt5'
WHERE sync_provider = 'metaapi';
```

and updates the column server default to `headless_mt5`.

- [ ] **Step 2: Verify the migration references the current head revision**

Run:

```powershell
Get-Content C:\Users\USER\Documents\SyncTrade\synctrades-be\alembic\versions\<new_revision>_migrate_metaapi_accounts_to_headless_mt5.py
```

Expected: correct `down_revision` and MT5-only data rewrite.

### Task 5: Add a regression test and run backend verification

**Files:**
- Modify: `C:/Users/USER/Documents/SyncTrade/synctrades-be/tests/test_mt5_sync_orchestrator.py`

- [ ] **Step 1: Add a regression check for MT5-only defaults**

Add a test that validates new account creation defaults to `headless_mt5` instead of `metaapi`.

- [ ] **Step 2: Run the backend test suite**

Run:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
$env:DATABASE_URL='sqlite://'
$env:SECRET_KEY='test-secret'
$env:ENCRYPTION_KEY='MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY='
uv run --with pytest --with pytest-httpx python -m pytest -p anyio.pytest_plugin -p pytest_httpx -q
```

Working directory:

```text
C:\Users\USER\Documents\SyncTrade\synctrades-be
```

Expected: test suite passes.

- [ ] **Step 3: Verify no MetaAPI references remain in backend code or env**

Run:

```powershell
rg -n "metaapi|MetaAPI|METAAPI_" C:\Users\USER\Documents\SyncTrade\synctrades-be\src C:\Users\USER\Documents\SyncTrade\synctrades-be\.env.example
```

Expected: no matches.

### Task 6: Review diff and commit

**Files:**
- Verify all modified backend files above

- [ ] **Step 1: Review the final backend diff**

Run:

```powershell
git -C C:\Users\USER\Documents\SyncTrade\synctrades-be diff -- .env.example src/app/core/config.py src/app/domains/accounts src/app/domains/journal src/app/tasks/journal_sync_tasks.py tests/test_mt5_sync_orchestrator.py alembic/versions
```

Expected: the diff shows only MetaAPI removal, MT5-only defaults, the data migration, and related wording cleanup.

- [ ] **Step 2: Commit the cleanup**

Run:

```powershell
git -C C:\Users\USER\Documents\SyncTrade\synctrades-be add .env.example src/app/core/config.py src/app/domains/accounts src/app/domains/journal src/app/tasks/journal_sync_tasks.py tests/test_mt5_sync_orchestrator.py alembic/versions
git -C C:\Users\USER\Documents\SyncTrade\synctrades-be commit -m "refactor: remove metaapi backend integration"
```

Expected: one clean commit containing the MT5-only backend cleanup.

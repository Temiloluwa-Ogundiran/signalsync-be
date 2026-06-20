# Copy Trading Control Plane Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tenant-safe database and API foundation for Telegram copy-trading connections, sources, account policies, routes, settings, pause controls, and immutable activity events without enabling live Telegram ingestion or broker execution yet.

**Architecture:** Add a focused `app.domains.copy_trading` domain to the existing FastAPI backend. PostgreSQL owns all durable control-plane state; services enforce ownership and state transitions; routers expose only authenticated user operations. Later plans will add Telegram authorization, channel learning, signal parsing, durable execution, MT5 operations, and frontend workflows against these contracts.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL, Alembic, pytest

---

## Scope Boundary

This is plan 1 of the approved copy-trading program. It produces a deployable, testable control plane with no live automation. Keep the copy-trading feature flag off until the later execution and reconciliation plans are complete.

Follow-up plans, in order:

1. Telegram session authentication and source discovery
2. Channel learning and reusable parser profiles
3. Signal assembly, AI interpretation, and deterministic validation
4. Durable intents, idempotency, account serialization, and reconciliation
5. MT5 pending orders, symbol catalogs, partial close, and execution contracts
6. Activity, notifications, settings, and emergency-control frontend
7. Reliability simulations, observability, feature flags, and Railway rollout

## File Map

### Create

- `src/app/domains/copy_trading/__init__.py`: domain package marker.
- `src/app/domains/copy_trading/models.py`: enums and six durable control-plane models.
- `src/app/domains/copy_trading/schemas.py`: public request/response contracts and cross-field settings validation.
- `src/app/domains/copy_trading/repository.py`: tenant-scoped persistence queries only.
- `src/app/domains/copy_trading/service.py`: ownership, route invariants, pause transitions, and activity creation.
- `src/app/domains/copy_trading/router.py`: authenticated `/copy-trading` endpoints.
- `alembic/versions/e7f8a9b0c1d2_add_copy_trading_control_plane.py`: schema migration after current head `d2e3f4a5b6c7`.
- `tests/test_copy_trading_models.py`: metadata and enum contract tests.
- `tests/test_copy_trading_migration.py`: revision graph and table/index contract tests.
- `tests/test_copy_trading_schemas.py`: Pydantic cross-field invariant tests.
- `tests/test_copy_trading_repository.py`: tenant-filter query tests.
- `tests/test_copy_trading_service.py`: ownership and state-transition tests.
- `tests/test_copy_trading_router.py`: authenticated endpoint contract tests.

### Modify

- `src/app/models/__init__.py`: import new models for Alembic metadata discovery.
- `src/app/main.py`: register the copy-trading router.
- `tests/conftest.py`: add no settings; retain existing environment defaults.

## Public Contract

The foundation exposes:

- `GET /copy-trading/settings`
- `PATCH /copy-trading/settings`
- `GET /copy-trading/account-policies`
- `PATCH /copy-trading/account-policies/{account_id}`
- `GET /copy-trading/routes`
- `POST /copy-trading/routes`
- `GET /copy-trading/routes/{route_id}`
- `PATCH /copy-trading/routes/{route_id}`
- `POST /copy-trading/routes/{route_id}/pause`
- `POST /copy-trading/routes/{route_id}/resume`
- `GET /copy-trading/activity`

Telegram connections and sources are persisted in this plan but receive no public mutation endpoint. The Telegram authentication plan owns those workflows so session material never enters a generic CRUD API.

---

### Task 1: Define The Control-Plane Models

**Files:**
- Create: `src/app/domains/copy_trading/__init__.py`
- Create: `src/app/domains/copy_trading/models.py`
- Modify: `src/app/models/__init__.py`
- Test: `tests/test_copy_trading_models.py`

- [ ] **Step 1: Write the failing metadata test**

```python
# tests/test_copy_trading_models.py
from sqlalchemy import inspect

from app.domains.copy_trading.models import (
    CopyAccountPolicy,
    CopyActivityEvent,
    CopyRoute,
    CopyRouteState,
    CopyTradingUserSettings,
    TelegramConnection,
    TelegramSource,
)


def test_copy_trading_tables_and_route_defaults_are_declared() -> None:
    assert {
        CopyTradingUserSettings.__tablename__,
        TelegramConnection.__tablename__,
        TelegramSource.__tablename__,
        CopyAccountPolicy.__tablename__,
        CopyRoute.__tablename__,
        CopyActivityEvent.__tablename__,
    } == {
        "copy_trading_user_settings",
        "telegram_connections",
        "telegram_sources",
        "copy_account_policies",
        "copy_routes",
        "copy_activity_events",
    }

    columns = {column.name for column in inspect(CopyRoute).columns}
    assert {"user_id", "source_id", "target_account_id", "magic_number"} <= columns
    assert {"fixed_lot", "take_profit_mode", "lot_distribution"} <= columns
    assert CopyRouteState.draft.value == "draft"
    assert CopyRouteState.paused.value == "paused"
```

- [ ] **Step 2: Run the test and verify the domain is missing**

Run: `uv run pytest tests/test_copy_trading_models.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.domains.copy_trading'`.

- [ ] **Step 3: Add enums and focused SQLAlchemy models**

Create `models.py` with these exact enum values and model responsibilities:

```python
class TelegramConnectionState(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    reauthentication_required = "reauthentication_required"
    disconnected = "disconnected"


class TelegramSourceType(str, enum.Enum):
    channel = "channel"
    group = "group"


class TelegramSourceState(str, enum.Enum):
    draft = "draft"
    learning = "learning"
    ready = "ready"
    active = "active"
    paused = "paused"
    unsupported = "unsupported"


class CopyRouteState(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    active = "active"
    paused = "paused"
    reauthentication_required = "reauthentication_required"
    unsupported = "unsupported"
    target_unavailable = "target_unavailable"


class TakeProfitMode(str, enum.Enum):
    all = "all"
    lowest = "lowest"
    highest = "highest"


class LotDistribution(str, enum.Enum):
    split_total = "split_total"
    fixed_each = "fixed_each"


class MinimumFields(str, enum.Enum):
    direction_symbol = "direction_symbol"
    direction_symbol_entry = "direction_symbol_entry"
    direction_symbol_sl = "direction_symbol_sl"
    direction_symbol_tp = "direction_symbol_tp"
    direction_symbol_sl_tp = "direction_symbol_sl_tp"


class CopyActivityLevel(str, enum.Enum):
    info = "info"
    success = "success"
    warning = "warning"
    error = "error"
```

Define the six models with UUID primary keys, timezone-aware timestamps, and these constraints:

```python
class CopyTradingUserSettings(Base):
    __tablename__ = "copy_trading_user_settings"
    user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    is_paused = mapped_column(Boolean, nullable=False, default=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class TelegramConnection(Base):
    __tablename__ = "telegram_connections"
    __table_args__ = (UniqueConstraint("user_id", "telegram_user_id"),)
    id = uuid_pk()
    user_id = tenant_fk()
    telegram_user_id = mapped_column(BigInteger, nullable=True)
    phone_hint = mapped_column(String(32), nullable=True)
    encrypted_session = mapped_column(Text, nullable=True)
    state = enum_column(TelegramConnectionState, "telegramconnectionstateenum", TelegramConnectionState.pending)
    last_heartbeat_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = created_at_column()
    updated_at = updated_at_column()


class TelegramSource(Base):
    __tablename__ = "telegram_sources"
    __table_args__ = (UniqueConstraint("connection_id", "telegram_chat_id"),)
    id = uuid_pk()
    user_id = tenant_fk()
    connection_id = mapped_column(UUID(as_uuid=True), ForeignKey("telegram_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_chat_id = mapped_column(BigInteger, nullable=False)
    title = mapped_column(String(255), nullable=False)
    username = mapped_column(String(255), nullable=True)
    source_type = enum_column(TelegramSourceType, "telegramsourcetypeenum", None)
    state = enum_column(TelegramSourceState, "telegramsourcestateenum", TelegramSourceState.draft)
    unsupported_reason = mapped_column(Text, nullable=True)
    created_at = created_at_column()
    updated_at = updated_at_column()


class CopyAccountPolicy(Base):
    __tablename__ = "copy_account_policies"
    __table_args__ = (UniqueConstraint("user_id", "account_id"), CheckConstraint("max_lot > 0", name="ck_copy_account_policy_max_lot_positive"))
    id = uuid_pk()
    user_id = tenant_fk()
    account_id = mapped_column(UUID(as_uuid=True), ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    max_lot = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("100.0000"))
    is_paused = mapped_column(Boolean, nullable=False, default=False)
    created_at = created_at_column()
    updated_at = updated_at_column()
```

`CopyRoute` must include tenant/source/account ownership, a unique `(user_id, source_id, target_account_id)` constraint, unique positive `magic_number`, `fixed_lot`, TP/lot mode, pending-order toggle, minimum fields, optional 1-600 second assembly override, author filter, success/failure email toggles, and booleans for SL/TP edits, break-even, additional TP, partial close, full close, and pending cancellation. Add `unsafe_minimum_confirmed_at`, `paused_from_state`, and timestamps.

`CopyActivityEvent` must include `user_id`, nullable route/source/account IDs, `correlation_id`, `action`, `level`, `title`, nullable `body`, `parsed_details` JSONB, `broker_details` JSONB, nullable encrypted raw message, and `created_at`. Index `(user_id, created_at)`, `(route_id, created_at)`, and `correlation_id`.

Use small local helpers (`uuid_pk`, `tenant_fk`, `enum_column`, timestamp columns) only to remove repeated SQLAlchemy declarations; do not create a shared framework.

Import and export all six models and enums from `src/app/models/__init__.py` so Alembic sees them.

- [ ] **Step 4: Run the model test**

Run: `uv run pytest tests/test_copy_trading_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the model contract**

```bash
git add src/app/domains/copy_trading src/app/models/__init__.py tests/test_copy_trading_models.py
git commit -m "feat: define copy trading control plane models"
```

---

### Task 2: Create The Alembic Migration

**Files:**
- Create: `alembic/versions/e7f8a9b0c1d2_add_copy_trading_control_plane.py`
- Create: `tests/test_copy_trading_migration.py`

- [ ] **Step 1: Write the failing revision and schema-contract tests**

```python
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_copy_trading_revision_is_the_single_head() -> None:
    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert script.get_current_head() == "e7f8a9b0c1d2"
    revision = script.get_revision("e7f8a9b0c1d2")
    assert revision is not None
    assert revision.down_revision == "d2e3f4a5b6c7"


def test_copy_trading_migration_declares_all_control_plane_tables() -> None:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "e7f8a9b0c1d2_add_copy_trading_control_plane.py"
    text = path.read_text(encoding="utf-8")
    for table in (
        "copy_trading_user_settings",
        "telegram_connections",
        "telegram_sources",
        "copy_account_policies",
        "copy_routes",
        "copy_activity_events",
    ):
        assert f'"{table}"' in text
```

- [ ] **Step 2: Run the migration tests and verify failure**

Run: `uv run pytest tests/test_copy_trading_migration.py -q`

Expected: FAIL because revision `e7f8a9b0c1d2` does not exist.

- [ ] **Step 3: Write the explicit migration**

Set:

```python
revision = "e7f8a9b0c1d2"
down_revision = "d2e3f4a5b6c7"
```

In `upgrade()`, instantiate the eight named PostgreSQL enums from Task 1 and call each enum's `create(op.get_bind(), checkfirst=True)` method. Then call `op.create_table` for `copy_trading_user_settings`, `telegram_connections`, `telegram_sources`, `copy_account_policies`, `copy_routes`, and `copy_activity_events`, in that order.

Mirror every model column, foreign key, unique/check constraint, and index from Task 1. Use `postgresql.JSONB(astext_type=sa.Text())` for activity details and `sa.Numeric(12, 4)` for lots. In `downgrade()`, drop tables in reverse dependency order, then drop enum types in reverse creation order with `checkfirst=True`.

- [ ] **Step 4: Validate revision graph and migration tests**

Run: `uv run pytest tests/test_copy_trading_migration.py tests/test_alembic_revision_compatibility.py -q`

Expected: PASS with a single Alembic head.

- [ ] **Step 5: Apply the migration to a disposable/local database**

Run: `uv run alembic upgrade head`

Expected: migration logs end at `e7f8a9b0c1d2` without duplicate type, foreign-key, or index errors. Do not run this against Railway until the complete foundation test suite passes.

- [ ] **Step 6: Commit the migration**

```bash
git add alembic/versions/e7f8a9b0c1d2_add_copy_trading_control_plane.py tests/test_copy_trading_migration.py
git commit -m "feat: migrate copy trading control plane"
```

---

### Task 3: Define Route Settings And Response Schemas

**Files:**
- Create: `src/app/domains/copy_trading/schemas.py`
- Create: `tests/test_copy_trading_schemas.py`

- [ ] **Step 1: Write failing cross-field validation tests**

```python
from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.domains.copy_trading.models import LotDistribution, MinimumFields, TakeProfitMode
from app.domains.copy_trading.schemas import CopyRouteCreate, CopyRouteUpdate


def test_split_total_requires_all_take_profits() -> None:
    with pytest.raises(ValidationError, match="Split lot is available only"):
        CopyRouteCreate(
            source_id="11111111-1111-1111-1111-111111111111",
            target_account_id="22222222-2222-2222-2222-222222222222",
            fixed_lot=Decimal("0.10"),
            take_profit_mode=TakeProfitMode.lowest,
            lot_distribution=LotDistribution.split_total,
        )


def test_unsafe_minimum_requires_confirmation() -> None:
    with pytest.raises(ValidationError, match="confirmation"):
        CopyRouteCreate(
            source_id="11111111-1111-1111-1111-111111111111",
            target_account_id="22222222-2222-2222-2222-222222222222",
            fixed_lot=Decimal("0.10"),
            minimum_fields=MinimumFields.direction_symbol,
            unsafe_minimum_confirmed=False,
        )


def test_route_update_rejects_assembly_window_above_ten_minutes() -> None:
    with pytest.raises(ValidationError):
        CopyRouteUpdate(assembly_window_seconds=601)
```

- [ ] **Step 2: Run the schema tests and verify failure**

Run: `uv run pytest tests/test_copy_trading_schemas.py -q`

Expected: FAIL because `schemas.py` does not exist.

- [ ] **Step 3: Implement request schemas with one shared validator**

Define `CopyRouteSettingsBase`, `CopyRouteCreate`, and `CopyRouteUpdate`. Use `Decimal` constraints for lots and a model-level validator:

```python
UNSAFE_MINIMUM_FIELDS = {
    MinimumFields.direction_symbol,
    MinimumFields.direction_symbol_entry,
    MinimumFields.direction_symbol_sl,
    MinimumFields.direction_symbol_tp,
}


@model_validator(mode="after")
def validate_route_settings(self):
    if self.lot_distribution == LotDistribution.split_total and self.take_profit_mode != TakeProfitMode.all:
        raise ValueError("Split lot is available only when all take profits are enabled.")
    if self.minimum_fields in UNSAFE_MINIMUM_FIELDS and not self.unsafe_minimum_confirmed:
        raise ValueError("Unsafe minimum fields require explicit warning confirmation.")
    return self
```

Use `Field(gt=0, max_digits=12, decimal_places=4)` for `fixed_lot` and `Field(default=None, ge=1, le=600)` for the assembly override. Patch schemas must preserve omitted fields with `model_fields_set`; do not convert omissions to defaults.

Add `CopyTradingSettingsResponse/Update`, `CopyAccountPolicyResponse/Update`, `CopyRouteResponse`, and `CopyActivityResponse`, all with `model_config = {"from_attributes": True}`. Route responses must expose the risk-confirmation timestamp and every management permission.

- [ ] **Step 4: Run schema tests**

Run: `uv run pytest tests/test_copy_trading_schemas.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the schema contract**

```bash
git add src/app/domains/copy_trading/schemas.py tests/test_copy_trading_schemas.py
git commit -m "feat: validate copy route settings"
```

---

### Task 4: Add Tenant-Scoped Repository Operations

**Files:**
- Create: `src/app/domains/copy_trading/repository.py`
- Create: `tests/test_copy_trading_repository.py`

- [ ] **Step 1: Write failing tenant-isolation tests**

Use a mocked SQLAlchemy session and inspect the compiled query parameters:

```python
import uuid
from unittest.mock import MagicMock

from app.domains.copy_trading import repository


def test_get_route_for_user_filters_by_route_and_owner() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    route_id = uuid.uuid4()
    user_id = uuid.uuid4()

    repository.get_route_for_user(db, route_id=route_id, user_id=user_id)

    statement = db.execute.call_args.args[0]
    params = statement.compile().params
    assert route_id in params.values()
    assert user_id in params.values()


def test_list_activity_never_queries_without_user_id() -> None:
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = []
    user_id = uuid.uuid4()
    repository.list_activity_for_user(db, user_id=user_id, limit=50, before=None)
    assert user_id in db.execute.call_args.args[0].compile().params.values()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_copy_trading_repository.py -q`

Expected: FAIL because repository functions are missing.

- [ ] **Step 3: Implement narrow repository functions**

Implement these exact interfaces and query shapes:

```python
def get_or_create_user_settings(db: Session, *, user_id: UUID) -> CopyTradingUserSettings:
    settings = db.get(CopyTradingUserSettings, user_id)
    if settings is None:
        settings = CopyTradingUserSettings(user_id=user_id)
        db.add(settings)
        db.flush()
    return settings


def get_source_for_user(db: Session, *, source_id: UUID, user_id: UUID) -> TelegramSource | None:
    return db.execute(
        select(TelegramSource).where(
            TelegramSource.id == source_id,
            TelegramSource.user_id == user_id,
        )
    ).scalar_one_or_none()


def get_account_policy(db: Session, *, account_id: UUID, user_id: UUID) -> CopyAccountPolicy | None:
    return db.execute(
        select(CopyAccountPolicy).where(
            CopyAccountPolicy.account_id == account_id,
            CopyAccountPolicy.user_id == user_id,
        )
    ).scalar_one_or_none()


def get_or_create_account_policy(db: Session, *, account_id: UUID, user_id: UUID) -> CopyAccountPolicy:
    policy = get_account_policy(db, account_id=account_id, user_id=user_id)
    if policy is None:
        policy = CopyAccountPolicy(account_id=account_id, user_id=user_id)
        db.add(policy)
        db.flush()
    return policy


def get_route_for_user(db: Session, *, route_id: UUID, user_id: UUID) -> CopyRoute | None:
    return db.execute(
        select(CopyRoute).where(CopyRoute.id == route_id, CopyRoute.user_id == user_id)
    ).scalar_one_or_none()


def create_route(db: Session, *, route: CopyRoute) -> CopyRoute:
    db.add(route)
    db.flush()
    return route


def create_activity(db: Session, *, event: CopyActivityEvent) -> CopyActivityEvent:
    db.add(event)
    db.flush()
    return event
```

Also implement `list_account_policies`, `get_route_by_source_and_account`, `list_routes_for_user`, and `list_activity_for_user`. Every read of a tenant-owned row must include `user_id` in SQL, even when the caller already resolved a parent. Order routes and activity newest first. `list_activity_for_user` applies `created_at < before` only when a cursor is supplied and always applies `.limit(limit)`. `create_*` functions call `flush()` but never commit; services own transactions.

- [ ] **Step 4: Run repository tests**

Run: `uv run pytest tests/test_copy_trading_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Commit repository isolation**

```bash
git add src/app/domains/copy_trading/repository.py tests/test_copy_trading_repository.py
git commit -m "feat: add tenant safe copy trading repository"
```

---

### Task 5: Implement Route And Pause Services

**Files:**
- Create: `src/app/domains/copy_trading/service.py`
- Create: `tests/test_copy_trading_service.py`

- [ ] **Step 1: Write failing service-invariant tests**

```python
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.domains.accounts.models import ImportMethod, TradingAccountConnectionState, TradingPlatform
from app.domains.copy_trading.models import CopyRouteState, TelegramSourceState
from app.domains.copy_trading.schemas import CopyRouteCreate
from app.domains.copy_trading.service import create_route, pause_route, resume_route


def ready_account(user_id):
    account = MagicMock()
    account.id = uuid.uuid4()
    account.user_id = user_id
    account.platform = TradingPlatform.mt5
    account.import_method = ImportMethod.auto_sync
    account.connection_state = TradingAccountConnectionState.ready
    account.is_archived = False
    return account


@patch("app.domains.copy_trading.service.account_repo.get_account_by_id_for_user")
@patch("app.domains.copy_trading.service.repo")
def test_create_route_rejects_lot_above_account_cap(repo, get_account) -> None:
    user = MagicMock(id=uuid.uuid4())
    source = MagicMock(id=uuid.uuid4(), state=TelegramSourceState.ready)
    account = ready_account(user.id)
    repo.get_source_for_user.return_value = source
    repo.get_account_policy.return_value = MagicMock(max_lot=Decimal("0.50"))
    get_account.return_value = account

    with pytest.raises(HTTPException) as exc:
        create_route(MagicMock(), current_user=user, payload=CopyRouteCreate(
            source_id=source.id,
            target_account_id=account.id,
            fixed_lot=Decimal("1.00"),
        ))
    assert exc.value.status_code == 422


@patch("app.domains.copy_trading.service.repo")
def test_pause_and_resume_preserve_previous_route_state(repo) -> None:
    user = MagicMock(id=uuid.uuid4())
    route = MagicMock(id=uuid.uuid4(), state=CopyRouteState.active, paused_from_state=None)
    repo.get_route_for_user.return_value = route
    repo.get_or_create_user_settings.return_value.is_paused = False
    repo.get_account_policy.return_value.is_paused = False
    db = MagicMock()
    pause_route(db, current_user=user, route_id=route.id)
    assert route.state == CopyRouteState.paused
    assert route.paused_from_state == CopyRouteState.active
    resume_route(db, current_user=user, route_id=route.id)
    assert route.state == CopyRouteState.active
```

- [ ] **Step 2: Run service tests and verify failure**

Run: `uv run pytest tests/test_copy_trading_service.py -q`

Expected: FAIL because service functions are missing.

- [ ] **Step 3: Implement service invariants and transactions**

Implement `get_user_settings`, `update_user_settings`, `list_account_policies`, `update_account_policy`, `list_routes`, `get_route`, `create_route`, `update_route`, `pause_route`, `resume_route`, `list_activity`, and `record_activity`. Every public service accepts `db` and `current_user`; resource operations additionally accept the UUID from the route. `record_activity` is the internal exception: it accepts explicit tenant/resource IDs and event fields because workers will call it without a FastAPI user object.

Use one ownership helper and one transaction pattern:

```python
def _owned_route(db: Session, *, current_user: User, route_id: uuid.UUID) -> CopyRoute:
    route = repo.get_route_for_user(db, route_id=route_id, user_id=current_user.id)
    if route is None:
        raise HTTPException(status_code=404, detail="Copy route not found.")
    return route


def pause_route(db: Session, *, current_user: User, route_id: uuid.UUID) -> CopyRoute:
    route = _owned_route(db, current_user=current_user, route_id=route_id)
    if route.state != CopyRouteState.paused:
        route.paused_from_state = route.state
        route.state = CopyRouteState.paused
        record_activity(
            db,
            user_id=current_user.id,
            route_id=route.id,
            source_id=route.source_id,
            account_id=route.target_account_id,
            correlation_id=str(uuid.uuid4()),
            action="route.paused",
            title="Copying paused",
            level=CopyActivityLevel.warning,
        )
        db.commit()
        db.refresh(route)
    return route
```

`create_route` must:

1. Resolve source with `get_source_for_user`.
2. Resolve target with `account_repo.get_account_by_id_for_user`.
3. Require MT5, `auto_sync`, non-archived, and `connection_state == ready`.
4. Reject duplicate source/account routes with HTTP 409.
5. Resolve/create the account policy and reject `fixed_lot > max_lot` with HTTP 422.
6. Generate the UUID first and derive a stable positive signed-31-bit magic number:

```python
def magic_number_for_route(route_id: uuid.UUID) -> int:
    return (int.from_bytes(route_id.bytes[:4], "big") & 0x7FFFFFFF) or 1
```

7. Start in `ready` when the source is ready, otherwise `draft`.
8. Set `unsafe_minimum_confirmed_at` only when confirmation was required and supplied.
9. Create a `route.created` activity event in the same transaction.
10. Commit once and refresh the route.

Patch only fields in `payload.model_fields_set`. Re-run the complete schema invariant against the merged current-and-patched values before mutation. Update account policy and route atomically when enforcing max lot. Route pause stores `paused_from_state`; resume restores it, defaulting to `ready`, and never resumes while global/account pause is active.

`update_account_policy` must first resolve the MT5 account through `account_repo.get_account_by_id_for_user`. When lowering `max_lot`, query the user's routes for that account and reject HTTP 409 if any route has `fixed_lot` above the proposed cap; report the largest configured route lot in the error. Record `account_policy.updated` activity in the same transaction. `list_account_policies` returns only policies whose account still belongs to the authenticated user.

- [ ] **Step 4: Run service tests**

Run: `uv run pytest tests/test_copy_trading_service.py -q`

Expected: PASS.

- [ ] **Step 5: Commit services**

```bash
git add src/app/domains/copy_trading/service.py tests/test_copy_trading_service.py
git commit -m "feat: enforce copy route safety policies"
```

---

### Task 6: Expose Authenticated Control-Plane APIs

**Files:**
- Create: `src/app/domains/copy_trading/router.py`
- Modify: `src/app/main.py`
- Create: `tests/test_copy_trading_router.py`

- [ ] **Step 1: Write failing router registration and delegation tests**

```python
from unittest.mock import MagicMock, patch
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.shared.deps import get_current_user


def test_copy_trading_routes_require_authentication() -> None:
    response = TestClient(app).get("/copy-trading/routes")
    assert response.status_code == 401


@patch("app.domains.copy_trading.router.service.list_routes", return_value=[])
def test_list_routes_uses_authenticated_user(list_routes) -> None:
    user = MagicMock(id=uuid.uuid4())
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get("/copy-trading/routes")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == []
    assert list_routes.call_args.kwargs["current_user"] is user
```

- [ ] **Step 2: Run router tests and verify failure**

Run: `uv run pytest tests/test_copy_trading_router.py -q`

Expected: FAIL with 404 because the router is not registered.

- [ ] **Step 3: Implement thin authenticated routes**

Create `APIRouter(prefix="/copy-trading", tags=["copy-trading"])`. Every endpoint depends on `get_db` and `get_current_user`, delegates to the service, and uses the Task 3 response models. Do not place ownership or state rules in the router.

Use exact status behavior and service delegation:

```python
@router.post("/routes", response_model=CopyRouteResponse, status_code=201)
def create_route(
    payload: CopyRouteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.create_route(db, current_user=current_user, payload=payload)
    return CopyRouteResponse.model_validate(route)

@router.post("/routes/{route_id}/pause", response_model=CopyRouteResponse)
def pause_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.pause_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)

@router.post("/routes/{route_id}/resume", response_model=CopyRouteResponse)
def resume_route(
    route_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CopyRouteResponse:
    route = service.resume_route(db, current_user=current_user, route_id=route_id)
    return CopyRouteResponse.model_validate(route)
```

Clamp activity `limit` to 1-100 and accept an optional timezone-aware `before` cursor. `PATCH` endpoints return the updated resource. Register `copy_trading_router` beside the existing account and notification routers in `src/app/main.py`.

- [ ] **Step 4: Run router and existing auth tests**

Run: `uv run pytest tests/test_copy_trading_router.py tests/test_auth_email_and_verification.py -q`

Expected: PASS.

- [ ] **Step 5: Commit API registration**

```bash
git add src/app/domains/copy_trading/router.py src/app/main.py tests/test_copy_trading_router.py
git commit -m "feat: expose copy trading control plane api"
```

---

### Task 7: Verify The Foundation As One Deployable Slice

**Files:**
- Modify only if verification exposes a defect in files from Tasks 1-6.

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_copy_trading_models.py tests/test_copy_trading_migration.py tests/test_copy_trading_schemas.py tests/test_copy_trading_repository.py tests/test_copy_trading_service.py tests/test_copy_trading_router.py -q
```

Expected: all copy-trading foundation tests PASS.

- [ ] **Step 2: Run migration compatibility tests**

Run:

```bash
uv run pytest tests/test_alembic_revision_compatibility.py tests/test_connection_state_readiness_migration.py -q
```

Expected: PASS and `ScriptDirectory.get_current_head()` remains `e7f8a9b0c1d2`.

- [ ] **Step 3: Run the complete backend suite**

Run: `uv run pytest -q`

Expected: PASS. Investigate every failure; do not classify existing failures without reproducing them from the pre-change commit.

- [ ] **Step 4: Check formatting and staged diff**

Run: `git diff --check`

Expected: no output.

- [ ] **Step 5: Inspect OpenAPI contracts locally**

Run: `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`

Verify `/openapi.json` contains all endpoints listed in Public Contract, every operation requires OAuth2 authentication, route payloads expose no encrypted Telegram/session fields, and activity raw-message ciphertext is absent from response schemas.

- [ ] **Step 6: Create the final foundation commit if verification required fixes**

```bash
git add src/app/domains/copy_trading src/app/models/__init__.py src/app/main.py alembic/versions/e7f8a9b0c1d2_add_copy_trading_control_plane.py tests/test_copy_trading_*.py
git commit -m "test: verify copy trading control plane foundation"
```

Skip this commit when the working tree is already clean.

## Deployment Gate

After all checks pass:

1. Push the backend `staging` branch.
2. Let Railway deploy the backend image.
3. Run `alembic upgrade head` through the existing Railway migration command using `DATABASE_URL_DIRECT`.
4. Confirm `copy_trading_*` and `telegram_*` tables exist.
5. Smoke-test authenticated list/settings endpoints.
6. Keep the frontend feature flag off; this slice must not initiate Telegram or MT5 actions.

## Definition Of Done

- One Alembic head and a reversible migration.
- All six tables are tenant-owned and cascade safely.
- Route settings reject impossible TP/lot combinations and unconfirmed unsafe minimum fields.
- Routes cannot target another user's source or trading account.
- Fixed lots cannot exceed the account policy.
- Global, account, and route pause state is durable.
- Activity events are immutable through the public API.
- No endpoint exposes encrypted Telegram session or raw-message ciphertext.
- Focused and full backend tests pass.

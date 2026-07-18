import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.domains.billing.entitlements import (
    BillingPlan,
    BillingStatus,
    has_copy_access,
    has_journal_access,
    require_copy_account_capacity,
)
from app.domains.billing.webhooks import verify_bachs_signature


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def subscription(**overrides):
    values = {
        "plan": BillingPlan.copy,
        "status": BillingStatus.active,
        "copy_account_limit": 1,
        "current_period_end": NOW + timedelta(days=20),
        "grace_ends_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_copy_plan_includes_journal_access():
    item = subscription(plan=BillingPlan.copy)

    assert has_copy_access(item, now=NOW)
    assert has_journal_access(item, now=NOW)


def test_journal_plan_does_not_include_copy_access():
    item = subscription(plan=BillingPlan.journal, copy_account_limit=0)

    assert has_journal_access(item, now=NOW)
    assert not has_copy_access(item, now=NOW)


def test_past_due_access_expires_after_three_day_grace_period():
    in_grace = subscription(
        status=BillingStatus.past_due,
        grace_ends_at=NOW + timedelta(hours=1),
    )
    expired = subscription(
        status=BillingStatus.past_due,
        grace_ends_at=NOW - timedelta(seconds=1),
    )

    assert has_copy_access(in_grace, now=NOW)
    assert not has_journal_access(expired, now=NOW)


def test_canceled_subscription_has_no_access():
    item = subscription(status=BillingStatus.canceled)

    assert not has_journal_access(item, now=NOW)
    assert not has_copy_access(item, now=NOW)


def test_copy_account_capacity_rejects_the_next_account_at_the_plan_limit():
    item = subscription(copy_account_limit=2)

    require_copy_account_capacity(item, current_account_count=1, now=NOW)
    with pytest.raises(HTTPException) as exc_info:
        require_copy_account_capacity(item, current_account_count=2, now=NOW)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "copy_account_limit_reached"


def test_bachs_webhook_signature_uses_timestamp_and_raw_body():
    body = json.dumps({"id": "evt_123", "type": "customer.subscription.created"}).encode()
    timestamp = str(int(NOW.timestamp()))
    secret = "whsec_test"
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()

    assert verify_bachs_signature(
        raw_body=body,
        secret=secret,
        timestamp_header=timestamp,
        signature_header=signature,
        now=NOW,
    )
    assert not verify_bachs_signature(
        raw_body=body + b" ",
        secret=secret,
        timestamp_header=timestamp,
        signature_header=signature,
        now=NOW,
    )


def test_bachs_webhook_signature_rejects_stale_deliveries():
    body = b"{}"
    old_timestamp = str(int((NOW - timedelta(minutes=6)).timestamp()))
    signature = hmac.new(
        b"whsec_test", old_timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()

    assert not verify_bachs_signature(
        raw_body=body,
        secret="whsec_test",
        timestamp_header=old_timestamp,
        signature_header=signature,
        now=NOW,
    )


def test_bachs_webhook_signature_rejects_invalid_headers():
    assert not verify_bachs_signature(
        raw_body=b"{}",
        secret="whsec_test",
        timestamp_header="not-a-timestamp",
        signature_header="bad",
        now=NOW,
    )


def test_plan_catalog_requires_all_copy_tiers(monkeypatch):
    from app.domains.billing.catalog import ProductCatalog

    monkeypatch.setattr(
        "app.domains.billing.catalog.settings.BACHS_COPY_PRODUCT_IDS",
        json.dumps({str(index): f"prod_copy_{index}" for index in range(1, 11)}),
    )
    monkeypatch.setattr(
        "app.domains.billing.catalog.settings.BACHS_JOURNAL_PRODUCT_ID",
        "prod_journal",
    )

    catalog = ProductCatalog.from_settings()

    assert catalog.product_for(BillingPlan.journal, 0) == "prod_journal"
    assert catalog.product_for(BillingPlan.copy, 10) == "prod_copy_10"
    assert catalog.entitlement_for("prod_copy_4") == (BillingPlan.copy, 4)


def test_plan_catalog_rejects_incomplete_copy_tiers(monkeypatch):
    from app.domains.billing.catalog import ProductCatalog

    monkeypatch.setattr(
        "app.domains.billing.catalog.settings.BACHS_COPY_PRODUCT_IDS",
        json.dumps({"1": "prod_copy_1"}),
    )
    monkeypatch.setattr(
        "app.domains.billing.catalog.settings.BACHS_JOURNAL_PRODUCT_ID",
        "prod_journal",
    )

    with pytest.raises(ValueError, match="1 through 10"):
        ProductCatalog.from_settings()


@pytest.mark.anyio
async def test_bachs_checkout_is_usd_card_only_and_links_the_user(httpx_mock):
    from app.domains.billing.client import BachsClient

    httpx_mock.add_response(
        method="POST",
        url="https://sandbox-api.bachs.io/v1/checkout-sessions",
        status_code=201,
        json={
            "checkout_id": "chk_123",
            "checkout_url": "https://checkout.bachs.io/c/chk_123",
            "status": "OPEN",
        },
    )
    user_id = uuid4()
    result = await BachsClient(api_key="sk_sandbox_test").create_checkout(
        product_id="prod_journal",
        user_id=user_id,
        email="trader@example.com",
        name="Trader",
        success_url="https://tradepartna.com/settings/subscription?checkout=success",
        cancel_url="https://tradepartna.com/settings/subscription?checkout=cancelled",
    )

    request = httpx_mock.get_request()
    payload = json.loads(request.content)
    assert result["checkout_url"] == "https://checkout.bachs.io/c/chk_123"
    assert payload["billing_currency"] == "USD"
    assert payload["allowed_payment_method_types"] == ["card"]
    assert payload["metadata"]["tradepartna_user_id"] == str(user_id)
    assert request.headers["Authorization"] == "Bearer sk_sandbox_test"
    assert request.headers["Idempotency-Key"]


def test_pending_downgrade_keeps_current_access_until_period_end():
    from app.domains.billing.service import subscription_response

    item = subscription(
        plan=BillingPlan.copy,
        copy_account_limit=3,
        pending_plan=BillingPlan.journal,
        pending_copy_account_limit=0,
        pending_effective_at=NOW + timedelta(days=1),
        cancel_at_period_end=False,
        grace_ends_at=None,
    )

    before = subscription_response(item, now=NOW)
    after = subscription_response(item, now=NOW + timedelta(days=2))

    assert before.has_copy_access
    assert before.copy_account_limit == 3
    assert after.has_journal_access
    assert not after.has_copy_access
    assert after.plan == BillingPlan.journal


@pytest.mark.anyio
async def test_checkout_guard_reuses_an_open_checkout(monkeypatch):
    from app.domains.billing import checkout_guard

    values: dict[str, str] = {}

    class FakeRedis:
        async def set(self, key, value, *, ex, nx=False):
            if nx and key in values:
                return False
            values[key] = value
            return True

        async def get(self, key):
            return values.get(key)

        async def delete(self, key):
            values.pop(key, None)

    monkeypatch.setattr(checkout_guard, "get_redis", lambda: FakeRedis())
    user_id = uuid4()

    assert await checkout_guard.acquire(user_id) is None
    assert await checkout_guard.acquire(user_id) == "creating"
    await checkout_guard.store(user_id, "https://checkout.bachs.io/c/chk_123")
    assert await checkout_guard.acquire(user_id) == "https://checkout.bachs.io/c/chk_123"


def test_duplicate_subscription_webhook_does_not_replace_active_subscription(monkeypatch):
    from app.domains.billing import service

    user = SimpleNamespace(id=uuid4())
    active = SimpleNamespace(
        status=BillingStatus.active,
        provider_subscription_id="sub_active",
    )

    class FakeDb:
        def commit(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(service.repo, "claim_webhook_event", lambda *args, **kwargs: True)
    monkeypatch.setattr(service.repo, "get_subscription_by_provider_id", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.repo, "get_subscription_for_user", lambda *args, **kwargs: active)
    monkeypatch.setattr(service, "_user_for_new_subscription", lambda *args, **kwargs: user)

    processed = service.process_webhook_event(
        FakeDb(),
        event={
            "id": "evt_duplicate",
            "type": "customer.subscription.created",
            "data": {"subscription_id": "sub_duplicate"},
        },
    )

    assert processed
    assert active.provider_subscription_id == "sub_active"

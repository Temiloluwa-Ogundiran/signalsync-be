from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.models  # noqa: F401 - load the complete SQLAlchemy relationship registry
from app.domains.affiliates import service
from app.domains.affiliates.models import AffiliateStatus, CommissionStatus


def profile(*, user_id=None, code="PARTNA2026", rate=None, status="active"):
    return SimpleNamespace(
        user_id=user_id or uuid4(),
        code=code,
        commission_rate_override=rate,
        status=status,
    )


def settings(*, rate=Decimal("20.00"), hold_days=30, months=12):
    return SimpleNamespace(
        default_commission_rate=rate,
        commission_hold_days=hold_days,
        recurring_months=months,
        minimum_payout=Decimal("25.00"),
    )


def test_effective_rate_prefers_individual_override():
    assert service.effective_rate(profile(rate=Decimal("27.50")), settings()) == Decimal("27.50")
    assert service.effective_rate(profile(rate=None), settings(rate=Decimal("20.00"))) == Decimal("20.00")


def test_attribute_rejects_self_referral(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    own_profile = profile(user_id=user.id)
    monkeypatch.setattr(service, "ensure_profile", lambda *_args, **_kwargs: own_profile)
    monkeypatch.setattr(service.repo, "get_attribution_for_user", lambda *_args: None)
    monkeypatch.setattr(service.repo, "get_profile_by_code", lambda *_args: own_profile)

    assert service.attribute_new_user(object(), referred_user=user, referral_code=own_profile.code) is None


def test_paid_invoice_creates_one_commission_with_rate_snapshot(monkeypatch):
    referred_user_id, affiliate_user_id = uuid4(), uuid4()
    created = []

    class FakeDb:
        def add(self, item):
            created.append(item)

        def flush(self):
            pass

    monkeypatch.setattr(service.repo, "get_commission_by_invoice", lambda *_args: None)
    monkeypatch.setattr(
        service.billing_repo,
        "get_subscription_by_provider_id",
        lambda *_args, **_kwargs: SimpleNamespace(user_id=referred_user_id, currency="USD"),
    )
    monkeypatch.setattr(
        service.repo,
        "get_attribution_for_user",
        lambda *_args: SimpleNamespace(affiliate_user_id=affiliate_user_id),
    )
    monkeypatch.setattr(service.repo, "get_profile", lambda *_args: profile(user_id=affiliate_user_id, rate=Decimal("27.50")))
    monkeypatch.setattr(service, "get_or_create_settings", lambda *_args: settings())
    monkeypatch.setattr(service.repo, "count_qualifying_commissions", lambda *_args: 0)

    item = service.create_commission_from_paid_invoice(
        FakeDb(),
        {"id": "inv_001", "subscription_id": "sub_001", "amount_paid": "30.00", "currency": "USD"},
    )

    assert item is created[0]
    assert item.commission_base == Decimal("30.00")
    assert item.commission_rate == Decimal("27.50")
    assert item.commission_amount == Decimal("8.25")
    assert item.status == CommissionStatus.pending.value


def test_paid_invoice_stops_after_recurring_month_limit(monkeypatch):
    monkeypatch.setattr(service.repo, "get_commission_by_invoice", lambda *_args: None)
    monkeypatch.setattr(service.billing_repo, "get_subscription_by_provider_id", lambda *_args, **_kwargs: SimpleNamespace(user_id=uuid4(), currency="USD"))
    monkeypatch.setattr(service.repo, "get_attribution_for_user", lambda *_args: SimpleNamespace(affiliate_user_id=uuid4()))
    monkeypatch.setattr(service.repo, "get_profile", lambda *_args: profile())
    monkeypatch.setattr(service, "get_or_create_settings", lambda *_args: settings(months=12))
    monkeypatch.setattr(service.repo, "count_qualifying_commissions", lambda *_args: 12)

    assert service.create_commission_from_paid_invoice(
        object(), {"id": "inv_limit", "subscription_id": "sub_limit", "amount": "17.00"}
    ) is None


def test_refund_reverses_pending_commission(monkeypatch):
    item = SimpleNamespace(
        status=CommissionStatus.pending.value,
        commission_base=Decimal("30.00"),
        commission_amount=Decimal("6.00"),
        reversed_amount=Decimal("0.00"),
        reversed_at=None,
        reversal_reason=None,
    )
    monkeypatch.setattr(service.repo, "get_commission_for_reversal", lambda *_args, **_kwargs: item)

    class FakeDb:
        def add(self, _item):
            pass

    result = service.reverse_commission(FakeDb(), {"invoice_id": "inv_001", "amount": "30.00"}, reason="refund")

    assert result is item
    assert item.reversed_amount == Decimal("6.00")
    assert item.status == CommissionStatus.reversed.value
    assert item.reversal_reason == "refund"

import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.billing import repository as repo
from app.domains.billing import checkout_guard
from app.domains.billing.catalog import ProductCatalog
from app.domains.billing.client import BachsClient, BachsError
from app.domains.billing.entitlements import has_copy_access, has_journal_access
from app.domains.billing.models import (
    BillingPlan,
    BillingStatus,
    BillingSubscription,
)
from app.domains.billing.schemas import SubscriptionResponse
from app.domains.users import repository as user_repo
from app.domains.users.models import User


SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_subscription(db: Session, *, user_id: uuid.UUID) -> BillingSubscription | None:
    return repo.get_subscription_for_user(db, user_id=user_id)


def _effective_values(
    subscription: BillingSubscription, *, now: datetime
) -> tuple[BillingPlan, int]:
    if (
        subscription.pending_plan is not None
        and subscription.pending_effective_at is not None
        and subscription.pending_effective_at <= now
    ):
        return subscription.pending_plan, subscription.pending_copy_account_limit or 0
    return subscription.plan, subscription.copy_account_limit


def effective_subscription(subscription: BillingSubscription) -> Any:
    plan, account_limit = _effective_values(subscription, now=utcnow())
    return type(
        "EffectiveSubscription",
        (),
        {
            "plan": plan,
            "status": subscription.status,
            "copy_account_limit": account_limit,
            "current_period_end": subscription.current_period_end,
            "grace_ends_at": subscription.grace_ends_at,
        },
    )()


def subscription_response(
    subscription: BillingSubscription | None, *, now: datetime | None = None
) -> SubscriptionResponse:
    current = now or utcnow()
    if subscription is None:
        return SubscriptionResponse(has_journal_access=False, has_copy_access=False)
    plan, account_limit = _effective_values(subscription, now=current)
    effective = effective_subscription(subscription)
    effective.plan = plan
    effective.copy_account_limit = account_limit
    return SubscriptionResponse(
        plan=plan,
        status=subscription.status,
        has_journal_access=has_journal_access(effective, now=current),
        has_copy_access=has_copy_access(effective, now=current),
        copy_account_limit=account_limit,
        current_period_end=subscription.current_period_end,
        grace_ends_at=subscription.grace_ends_at,
        cancel_at_period_end=subscription.cancel_at_period_end,
        pending_plan=subscription.pending_plan,
        pending_copy_account_limit=subscription.pending_copy_account_limit,
        pending_effective_at=subscription.pending_effective_at,
    )


def _is_upgrade(
    subscription: BillingSubscription, *, target_plan: BillingPlan, target_accounts: int
) -> bool:
    if subscription.plan == BillingPlan.journal and target_plan == BillingPlan.copy:
        return True
    return (
        subscription.plan == BillingPlan.copy
        and target_plan == BillingPlan.copy
        and target_accounts > subscription.copy_account_limit
    )


async def start_checkout_or_change_plan(
    db: Session,
    *,
    current_user: User,
    plan: BillingPlan,
    copy_accounts: int,
    client: BachsClient | None = None,
) -> tuple[str, str | None, BillingSubscription | None]:
    catalog = ProductCatalog.from_settings()
    target_accounts = copy_accounts if plan == BillingPlan.copy else 0
    product_id = catalog.product_for(plan, target_accounts)
    subscription = get_subscription(db, user_id=current_user.id)
    provider = client or BachsClient()
    if subscription is None or subscription.status in {BillingStatus.canceled, BillingStatus.unpaid}:
        try:
            open_checkout = await checkout_guard.acquire(current_user.id)
        except checkout_guard.CheckoutGuardUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if open_checkout == "creating":
            raise HTTPException(
                status_code=409,
                detail="A checkout is already being prepared. Please wait a moment.",
            )
        if open_checkout:
            return "checkout", open_checkout, subscription
        try:
            result = await provider.create_checkout(
                product_id=product_id,
                user_id=current_user.id,
                email=current_user.email,
                name=current_user.display_name,
                success_url=f"{settings.FRONTEND_URL.rstrip('/')}/settings/subscription?checkout=success",
                cancel_url=f"{settings.FRONTEND_URL.rstrip('/')}/settings/subscription?checkout=cancelled",
            )
        except BachsError as exc:
            await checkout_guard.release(current_user.id)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        checkout_url = result.get("checkout_url")
        if not isinstance(checkout_url, str) or not checkout_url.startswith("https://"):
            await checkout_guard.release(current_user.id)
            raise HTTPException(status_code=502, detail="The billing provider returned no checkout URL")
        try:
            await checkout_guard.store(current_user.id, checkout_url)
        except checkout_guard.CheckoutGuardUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return "checkout", checkout_url, subscription

    if subscription.plan == plan and subscription.copy_account_limit == target_accounts:
        raise HTTPException(status_code=409, detail="This subscription plan is already active.")

    upgrade = _is_upgrade(subscription, target_plan=plan, target_accounts=target_accounts)
    try:
        result = await provider.change_plan(
            subscription_id=subscription.provider_subscription_id,
            product_id=product_id,
            proration_behavior="invoice_now" if upgrade else "next_cycle",
        )
    except BachsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if upgrade:
        apply_subscription_payload(db, result, catalog=catalog, existing=subscription)
        db.commit()
        db.refresh(subscription)
        return "updated", None, subscription

    subscription.provider_product_id = product_id
    subscription.pending_plan = plan
    subscription.pending_copy_account_limit = target_accounts
    subscription.pending_effective_at = subscription.current_period_end
    db.commit()
    db.refresh(subscription)
    return "scheduled", None, subscription


async def cancel_subscription(
    db: Session, *, current_user: User, client: BachsClient | None = None
) -> BillingSubscription:
    subscription = get_subscription(db, user_id=current_user.id)
    if subscription is None or subscription.status == BillingStatus.canceled:
        raise HTTPException(status_code=404, detail="No active subscription was found.")
    if subscription.cancel_at_period_end:
        raise HTTPException(status_code=409, detail="Cancellation is already scheduled.")
    try:
        result = await (client or BachsClient()).cancel_at_period_end(
            subscription_id=subscription.provider_subscription_id
        )
    except BachsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    subscription.cancel_at_period_end = bool(result.get("cancel_at_period_end", True))
    subscription.current_period_end = parse_datetime(result.get("current_period_end")) or subscription.current_period_end
    db.commit()
    db.refresh(subscription)
    return subscription


def _status(value: Any, *, deleted: bool = False) -> BillingStatus:
    if deleted:
        return BillingStatus.canceled
    try:
        return BillingStatus(str(value))
    except ValueError as exc:
        raise ValueError("Unsupported Bachs subscription status") from exc


def apply_subscription_payload(
    db: Session,
    payload: dict[str, Any],
    *,
    catalog: ProductCatalog,
    existing: BillingSubscription | None = None,
    deleted: bool = False,
) -> BillingSubscription:
    provider_subscription_id = payload.get("subscription_id") or payload.get("id")
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    product_id = payload.get("product_id") or product.get("id")
    if not isinstance(provider_subscription_id, str) or not isinstance(product_id, str):
        raise ValueError("Bachs subscription payload is missing identifiers")
    plan, account_limit = catalog.entitlement_for(product_id)
    subscription = existing or repo.get_subscription_by_provider_id(
        db, provider_subscription_id=provider_subscription_id
    )
    if subscription is None:
        raise ValueError("Bachs subscription is not linked to a user")

    pending_matches = (
        subscription.pending_plan == plan
        and subscription.pending_copy_account_limit == account_limit
        and subscription.pending_effective_at is not None
        and subscription.pending_effective_at > utcnow()
    )
    subscription.provider_subscription_id = provider_subscription_id
    subscription.provider_product_id = product_id
    if not pending_matches:
        subscription.plan = plan
        subscription.copy_account_limit = account_limit
        subscription.pending_plan = None
        subscription.pending_copy_account_limit = None
        subscription.pending_effective_at = None
    subscription.status = _status(payload.get("status"), deleted=deleted)
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    subscription.provider_customer_id = customer.get("customer_id") or subscription.provider_customer_id
    subscription.currency = str(payload.get("currency") or subscription.currency or "USD")
    subscription.amount = str(payload.get("amount") or subscription.amount or "0.00")
    subscription.current_period_start = parse_datetime(payload.get("current_period_start"))
    subscription.current_period_end = parse_datetime(payload.get("current_period_end"))
    subscription.cancel_at_period_end = bool(payload.get("cancel_at_period_end", False))
    subscription.canceled_at = parse_datetime(payload.get("canceled_at"))
    if subscription.status == BillingStatus.past_due and subscription.grace_ends_at is None:
        subscription.grace_ends_at = utcnow() + timedelta(days=settings.BILLING_GRACE_DAYS)
    elif subscription.status == BillingStatus.active:
        subscription.grace_ends_at = None
    db.add(subscription)
    return subscription


def _user_for_new_subscription(db: Session, data: dict[str, Any]) -> User | None:
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    raw_user_id = metadata.get("tradepartna_user_id")
    if raw_user_id:
        try:
            user = user_repo.get_by_id(db, uuid.UUID(str(raw_user_id)))
        except (ValueError, TypeError):
            user = None
        if user is not None:
            return user
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    email = customer.get("email")
    return user_repo.get_by_email(db, str(email).lower()) if email else None


def process_webhook_event(db: Session, *, event: dict[str, Any]) -> bool:
    event_id = event.get("id")
    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(event_id, str) or not isinstance(event_type, str) or not isinstance(data, dict):
        raise ValueError("Invalid Bachs webhook envelope")
    if not repo.claim_webhook_event(
        db,
        provider_event_id=event_id,
        event_type=event_type,
        payload=event,
    ):
        return False
    try:
        if event_type in SUBSCRIPTION_EVENTS:
            provider_subscription_id = data.get("subscription_id") or data.get("id")
            subscription = repo.get_subscription_by_provider_id(
                db, provider_subscription_id=str(provider_subscription_id)
            ) if provider_subscription_id else None
            if subscription is None and event_type != "customer.subscription.created":
                logger.warning(
                    "Ignored unlinked Bachs subscription event type=%s subscription_id=%s",
                    event_type,
                    provider_subscription_id,
                )
                db.commit()
                return True
            if subscription is None and event_type == "customer.subscription.created":
                user = _user_for_new_subscription(db, data)
                if user is None:
                    raise ValueError("Bachs subscription customer is not a TradePartna user")
                subscription = repo.get_subscription_for_user(db, user_id=user.id)
                if (
                    subscription is not None
                    and subscription.status not in {BillingStatus.canceled, BillingStatus.unpaid}
                    and subscription.provider_subscription_id != str(provider_subscription_id)
                ):
                    logger.error(
                        "Ignored duplicate Bachs subscription user_id=%s active_subscription=%s duplicate_subscription=%s",
                        user.id,
                        subscription.provider_subscription_id,
                        provider_subscription_id,
                    )
                    db.commit()
                    return True
                if subscription is None:
                    catalog = ProductCatalog.from_settings()
                    product_id = data.get("product_id")
                    plan, account_limit = catalog.entitlement_for(str(product_id))
                    subscription = BillingSubscription(
                        user_id=user.id,
                        provider_subscription_id=str(provider_subscription_id),
                        provider_product_id=str(product_id),
                        plan=plan,
                        status=BillingStatus.active,
                        copy_account_limit=account_limit,
                        currency=str(data.get("currency") or "USD"),
                        amount=str(data.get("amount") or "0.00"),
                    )
            apply_subscription_payload(
                db,
                data,
                catalog=ProductCatalog.from_settings(),
                existing=subscription,
                deleted=event_type == "customer.subscription.deleted",
            )
        elif event_type == "invoice.payment_failed":
            nested = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
            provider_id = nested.get("subscription_id")
            subscription = repo.get_subscription_by_provider_id(db, provider_subscription_id=str(provider_id))
            if subscription is not None:
                subscription.status = BillingStatus.past_due
                if subscription.grace_ends_at is None:
                    subscription.grace_ends_at = utcnow() + timedelta(days=settings.BILLING_GRACE_DAYS)
        elif event_type == "invoice.paid":
            nested = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
            provider_id = nested.get("subscription_id")
            subscription = repo.get_subscription_by_provider_id(db, provider_subscription_id=str(provider_id))
            if subscription is not None and subscription.status != BillingStatus.canceled:
                subscription.status = BillingStatus.active
                subscription.grace_ends_at = None
        db.commit()
    except Exception as exc:
        db.rollback()
        raise exc
    return True

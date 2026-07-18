import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domains.billing.models import BillingSubscription, BillingWebhookEvent


def get_subscription_for_user(db: Session, *, user_id: uuid.UUID) -> Optional[BillingSubscription]:
    return db.execute(
        select(BillingSubscription).where(BillingSubscription.user_id == user_id)
    ).scalar_one_or_none()


def get_subscription_by_provider_id(
    db: Session, *, provider_subscription_id: str
) -> Optional[BillingSubscription]:
    return db.execute(
        select(BillingSubscription).where(
            BillingSubscription.provider_subscription_id == provider_subscription_id
        )
    ).scalar_one_or_none()


def claim_webhook_event(
    db: Session, *, provider_event_id: str, event_type: str, payload: dict
) -> bool:
    statement = (
        insert(BillingWebhookEvent)
        .values(
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=[BillingWebhookEvent.provider_event_id])
        .returning(BillingWebhookEvent.id)
    )
    return db.execute(statement).scalar_one_or_none() is not None

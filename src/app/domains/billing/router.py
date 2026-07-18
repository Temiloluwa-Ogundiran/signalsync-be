import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.domains.billing import service
from app.domains.billing.schemas import (
    BillingMessageResponse,
    CheckoutRequest,
    CheckoutResponse,
    SubscriptionResponse,
)
from app.domains.billing.webhooks import verify_bachs_signature
from app.domains.users.models import PlatformRole, User
from app.shared.deps import get_current_user


router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/me", response_model=SubscriptionResponse)
def get_my_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubscriptionResponse:
    if current_user.platform_role != PlatformRole.USER:
        return SubscriptionResponse(
            plan="copy",
            status="active",
            has_journal_access=True,
            has_copy_access=True,
            copy_account_limit=10,
        )
    return service.subscription_response(
        service.get_subscription(db, user_id=current_user.id)
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def start_checkout(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckoutResponse:
    action, checkout_url, subscription = await service.start_checkout_or_change_plan(
        db,
        current_user=current_user,
        plan=payload.plan,
        copy_accounts=payload.copy_accounts,
    )
    return CheckoutResponse(
        action=action,
        checkout_url=checkout_url,
        subscription=service.subscription_response(subscription),
    )


@router.delete("/subscription", response_model=BillingMessageResponse)
async def cancel_my_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BillingMessageResponse:
    subscription = await service.cancel_subscription(db, current_user=current_user)
    return BillingMessageResponse(
        message="Your subscription will remain active until the end of the paid period.",
        subscription=service.subscription_response(subscription),
    )


@router.post("/webhooks/bachs")
async def receive_bachs_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_bachs_timestamp: str = Header(default=""),
    x_bachs_signature: str = Header(default=""),
) -> dict[str, bool]:
    raw_body = await request.body()
    if not verify_bachs_signature(
        raw_body=raw_body,
        secret=settings.BACHS_WEBHOOK_SECRET,
        timestamp_header=x_bachs_timestamp,
        signature_header=x_bachs_signature,
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature.")
    try:
        event = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.") from exc
    processed = service.process_webhook_event(db, event=event)
    return {"received": True, "processed": processed}

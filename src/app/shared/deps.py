from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.domains.users.models import User
from app.domains.users.models import PlatformRole
from app.domains.users import repository as user_repo

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _resolve_current_user(token: str, db: Session) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_token(token)
    if payload is None or payload.get("typ") != "access":
        raise credentials_exception
    user_id: str = payload.get("sub")
    if not user_id:
        raise credentials_exception
    user = user_repo.get_by_id(db, user_id)
    if (
        not user
        or user.is_deleted
        or user.is_suspended
        or not user.is_email_verified
    ):
        raise credentials_exception
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    return _resolve_current_user(token, db)


def get_optional_current_user(
    token: str | None = Depends(optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    if not token:
        return None
    return _resolve_current_user(token, db)


def _is_billing_admin(user: User) -> bool:
    return user.platform_role in {
        PlatformRole.ADMIN,
        PlatformRole.TECHNICAL_ADMIN,
        PlatformRole.SUPER_ADMIN,
    }


def require_journal_access(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    from app.core.config import settings
    from app.domains.billing import service as billing_service
    from app.domains.billing.entitlements import BillingPlan, subscription_required_detail

    if not settings.BILLING_ENFORCED or _is_billing_admin(current_user):
        return current_user
    if request.method in {"GET", "HEAD", "OPTIONS", "DELETE"} or request.url.path.endswith("/disconnect"):
        return current_user
    subscription = billing_service.get_subscription(db, user_id=current_user.id)
    if not billing_service.subscription_response(subscription).has_journal_access:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=subscription_required_detail(BillingPlan.journal),
        )
    return current_user


def require_copy_access(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    from app.core.config import settings
    from app.domains.billing import service as billing_service
    from app.domains.billing.entitlements import BillingPlan, subscription_required_detail

    if not settings.BILLING_ENFORCED or _is_billing_admin(current_user):
        return current_user
    if (
        request.method in {"GET", "HEAD", "OPTIONS", "DELETE"}
        or request.url.path.endswith("/pause")
        or request.url.path.endswith("/emergency")
    ):
        return current_user
    subscription = billing_service.get_subscription(db, user_id=current_user.id)
    if not billing_service.subscription_response(subscription).has_copy_access:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=subscription_required_detail(BillingPlan.copy),
        )
    return current_user

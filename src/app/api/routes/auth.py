from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.auth import (
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    return auth_service.register(db, payload)


@router.get(
    "/verify-email",
    summary="Verify a user's email address via link",
    description=(
        "Called when the user clicks the verification link in their email. "
        "On success, redirects to the frontend (or mobile deep link if "
        "`platform=mobile` is passed)."
    ),
)
def verify_email(
    token: str = Query(..., description="Raw verification token from the email link"),
    platform: str = Query("web", description="'web' or 'mobile'"),
    db: Session = Depends(get_db),
):
    auth_service.verify_email(db, token)

    if platform == "mobile" and settings.DEEP_LINK_SCHEME:
        redirect_url = f"{settings.DEEP_LINK_SCHEME}email-verified"
    else:
        redirect_url = f"{settings.FRONTEND_URL}/email-verified"

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


@router.post(
    "/resend-verification",
    response_model=ResendVerificationResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend email verification link",
    description="Sends a fresh verification link. Always returns 200 to avoid leaking registered emails.",
)
def resend_verification(
    payload: ResendVerificationRequest, db: Session = Depends(get_db)
):
    return auth_service.resend_verification(db, payload)


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Log in and receive an access token",
)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # OAuth2PasswordRequestForm uses 'username' — we treat it as the email.
    return auth_service.login(db, form_data.username, form_data.password, response)

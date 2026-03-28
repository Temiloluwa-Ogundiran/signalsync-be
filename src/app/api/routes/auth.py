from fastapi import APIRouter, Cookie, Depends, Query, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.schemas.auth import (
    LoginResponse,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
    VerifyEmailResponse,
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
    response_model=VerifyEmailResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify a user's email address",
    description=(
        "The frontend reads the token from the URL query param and calls this "
        "endpoint. On success returns a JSON confirmation; the frontend handles "
        "any subsequent navigation."
    ),
)
def verify_email(
    token: str = Query(..., description="Raw verification token from the email link"),
    db: Session = Depends(get_db),
):
    return auth_service.verify_email(db, token)


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


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate refresh token and issue a new access token",
    description=(
        "Reads the `refresh_token` HTTP-only cookie. "
        "On success, revokes the old refresh token, issues a new one "
        "(rotation), and returns a fresh access token."
    ),
)
def refresh_token(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    return auth_service.refresh_access_token(db, refresh_token, response)


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Log out and revoke the refresh token",
    description="Revokes the refresh token cookie. Always returns 200.",
)
def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    return auth_service.logout(db, refresh_token, response)

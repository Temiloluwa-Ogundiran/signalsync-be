from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    File,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.auth.router import verify_origin
from app.shared.deps import get_current_user
from app.domains.users.models import User
from app.domains.users import service as user_service
from app.domains.users.schemas import (
    AvatarUploadResponse,
    ChangeEmailRequest,
    ChangePasswordRequest,
    CompleteOnboardingRequest,
    DeleteAccountRequest,
    MessageResponse,
    SessionResponse,
    SetPasswordRequest,
    UpdatePreferencesRequest,
    UpdateProfileRequest,
    UserResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the currently authenticated user",
)
def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update the current user's profile",
)
def update_me(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    user = user_service.update_profile(db, current_user=current_user, payload=payload)
    return UserResponse.model_validate(user)


@router.patch(
    "/me/preferences",
    response_model=UserResponse,
    summary="Update the current user's display preferences",
    description="Set display timezone and/or currency (formatting only — no conversion).",
)
def update_my_preferences(
    payload: UpdatePreferencesRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    user = user_service.update_preferences(
        db, current_user=current_user, payload=payload
    )
    return UserResponse.model_validate(user)


@router.patch(
    "/me/onboarding",
    response_model=UserResponse,
    summary="Save onboarding answers and mark onboarding complete",
)
def complete_my_onboarding(
    payload: CompleteOnboardingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    user = user_service.complete_onboarding(
        db, current_user=current_user, payload=payload
    )
    return UserResponse.model_validate(user)


@router.post(
    "/me/avatar",
    response_model=AvatarUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload the current user's avatar",
    description="Send as multipart/form-data with `file` (JPEG, PNG, WebP, or GIF).",
)
def upload_my_avatar(
    file: UploadFile = File(..., description="Avatar image"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AvatarUploadResponse:
    user = user_service.update_avatar(db, current_user=current_user, file=file)
    return AvatarUploadResponse(avatar_url=user.avatar_url or "")


@router.post(
    "/me/change-password",
    response_model=MessageResponse,
    summary="Change the current user's password",
    description="Requires the current password. Revokes all other sessions.",
)
def change_my_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    user_service.change_password(db, current_user=current_user, payload=payload)
    return MessageResponse(message="Password changed successfully.")


@router.post(
    "/me/set-password",
    response_model=MessageResponse,
    summary="Set an initial password for the current user",
    description=(
        "For accounts with no usable password (e.g. created via Google). No "
        "current password is required. Fails if the account already has one."
    ),
)
def set_my_password(
    payload: SetPasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    user_service.set_password(db, current_user=current_user, payload=payload)
    return MessageResponse(message="Password set successfully.")


@router.post(
    "/me/change-email",
    response_model=MessageResponse,
    summary="Change the current user's email",
    description=(
        "Requires the current password. Sets the new email as unverified and "
        "sends a verification link; the user must re-verify and log in again."
    ),
    dependencies=[Depends(verify_origin)],
)
def change_my_email(
    payload: ChangeEmailRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    user_service.change_email(db, current_user=current_user, payload=payload)
    return MessageResponse(
        message="Email updated. Please check your new inbox to verify it, then log in again."
    )


@router.get(
    "/me/sessions",
    response_model=list[SessionResponse],
    summary="List the current user's active sessions",
)
def list_my_sessions(
    current_user: User = Depends(get_current_user),
    refresh_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> list[SessionResponse]:
    return user_service.list_sessions(
        db, current_user=current_user, current_refresh_token=refresh_token
    )


@router.delete(
    "/me/sessions/{session_id}",
    response_model=MessageResponse,
    summary="Revoke one of the current user's sessions",
    dependencies=[Depends(verify_origin)],
)
def revoke_my_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    user_service.revoke_session(db, current_user=current_user, session_id=session_id)
    return MessageResponse(message="Session revoked.")


@router.delete(
    "/me",
    response_model=MessageResponse,
    summary="Delete (soft) the current user's account",
    description="Requires the current password. Revokes all sessions.",
    dependencies=[Depends(verify_origin)],
)
def delete_me(
    payload: DeleteAccountRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    user_service.delete_account(
        db, current_user=current_user, payload=payload, response=response
    )
    return MessageResponse(message="Your account has been deleted.")

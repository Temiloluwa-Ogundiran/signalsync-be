from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.user import UserResponse
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the currently authenticated user",
)
def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


class UsernameAvailabilityResponse(BaseModel):
    username: str
    available: bool


@router.get(
    "/check-username",
    response_model=UsernameAvailabilityResponse,
    summary="Check if a username is available",
    description=(
        "Lightweight endpoint for real-time (debounced) username availability "
        "checks during registration. No authentication required."
    ),
)
def check_username(
    username: str = Query(..., min_length=3, max_length=50, description="Username to check"),
    db: Session = Depends(get_db),
):
    available = user_service.is_username_available(db, username)
    return UsernameAvailabilityResponse(username=username, available=available)

from fastapi import APIRouter, Depends

from app.shared.deps import get_current_user
from app.domains.users.models import User
from app.domains.users.schemas import UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the currently authenticated user",
)
def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)

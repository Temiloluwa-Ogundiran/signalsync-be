import uuid
from typing import List
from fastapi import APIRouter, Depends, status, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.users.models import User
from app.shared.deps import get_current_user

from app.domains.journal.schemas_tags import (
    TagCategoryResponse,
    TagOptionResponse,
    CategoryCreateRequest,
    OptionCreateRequest,
    TradeTagUpdateRequest,
    TradeRatingUpdateRequest,
    TradeAssessmentUpdateRequest,
)
from app.domains.journal import service_tags as tags_service

router = APIRouter(tags=["journal-tags"])


@router.get("/journal/tags/config", response_model=List[TagCategoryResponse])
def get_tags_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[TagCategoryResponse]:
    """
    Fetch all visible categories and options for the current user.
    """
    categories = tags_service.list_user_tags_config(db, user=current_user)
    return [TagCategoryResponse.model_validate(c) for c in categories]


@router.post(
    "/journal/tags/categories",
    response_model=TagCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    payload: CategoryCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TagCategoryResponse:
    """
    Create a new custom category.
    """
    category = tags_service.create_custom_category(
        db, user=current_user, title=payload.title
    )
    return TagCategoryResponse.model_validate(category)


@router.delete(
    "/journal/tags/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_category(
    category_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    Delete a custom category owned by the user.
    """
    tags_service.delete_custom_category(db, user=current_user, category_id=category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/journal/tags/categories/{category_id}/options",
    response_model=TagOptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_option(
    category_id: uuid.UUID,
    payload: OptionCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TagOptionResponse:
    """
    Create a new option under a specific category.
    """
    option = tags_service.create_custom_option(
        db,
        user=current_user,
        category_id=category_id,
        value=payload.value,
        color=payload.color,
    )
    return TagOptionResponse.model_validate(option)


@router.delete(
    "/journal/tags/options/{option_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_option(
    option_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    Delete a custom option owned by the user.
    """
    tags_service.delete_custom_option(db, user=current_user, option_id=option_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/journal/trades/{trade_id}/tags",
    response_model=List[TagOptionResponse],
)
def get_trade_tags(
    trade_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[TagOptionResponse]:
    """
    Fetch all active tag selections for a specific trade.
    """
    options = tags_service.get_trade_tags(db, user=current_user, trade_id=trade_id)
    return [TagOptionResponse.model_validate(o) for o in options]


@router.put(
    "/journal/trades/{trade_id}/tags",
    response_model=List[TagOptionResponse],
)
def update_trade_tags(
    trade_id: uuid.UUID,
    payload: TradeTagUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[TagOptionResponse]:
    """
    Replace the active tag selections for a specific trade.
    """
    options = tags_service.update_trade_tags(
        db, user=current_user, trade_id=trade_id, option_ids=payload.option_ids
    )
    return [TagOptionResponse.model_validate(o) for o in options]


@router.put(
    "/journal/trades/{trade_id}/rating",
)
def update_trade_rating(
    trade_id: uuid.UUID,
    payload: TradeRatingUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the rating for a specific trade.
    """
    rating = tags_service.update_trade_rating(
        db, user=current_user, trade_id=trade_id, rating=payload.rating
    )
    return {"rating": rating}


@router.put(
    "/journal/trades/{trade_id}/assessment",
)
def update_trade_assessment(
    trade_id: uuid.UUID,
    payload: TradeAssessmentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the execution quality, setup quality, and discipline score for a specific trade.
    """
    return tags_service.update_trade_assessment(
        db,
        user=current_user,
        trade_id=trade_id,
        execution_quality=payload.execution_quality,
        setup_quality=payload.setup_quality,
        discipline_score=payload.discipline_score,
    )

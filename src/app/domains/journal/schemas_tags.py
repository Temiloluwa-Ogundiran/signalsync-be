import uuid
from typing import Optional, List
from pydantic import BaseModel, Field


class TagOptionResponse(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    value: str
    color: Optional[str] = None

    model_config = {"from_attributes": True}


class TagCategoryResponse(BaseModel):
    id: uuid.UUID
    title: str
    is_system: bool
    options: List[TagOptionResponse] = []

    model_config = {"from_attributes": True}


class CategoryCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class OptionCreateRequest(BaseModel):
    value: str = Field(..., min_length=1, max_length=100)
    color: Optional[str] = Field(default=None, max_length=7, pattern="^#([A-Fa-f0-9]{6})$")


class TradeTagUpdateRequest(BaseModel):
    option_ids: List[uuid.UUID]


class TradeRatingUpdateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)


class TradeAssessmentUpdateRequest(BaseModel):
    execution_quality: Optional[int] = Field(default=None, ge=0, le=10)
    setup_quality: Optional[int] = Field(default=None, ge=0, le=10)
    discipline_score: Optional[int] = Field(default=None, ge=0, le=10)

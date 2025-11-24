from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class HomeworkTaskBase(BaseModel):
    title: str = Field(..., max_length=255)
    detail: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=50)
    due_date: Optional[date] = None
    status: Optional[str] = Field(default=None, pattern="^(pending|done)$")


class HomeworkTaskCreate(HomeworkTaskBase):
    user_id: str
    conversation_id: Optional[str] = None
    status: Optional[str] = "pending"


class HomeworkTaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)
    detail: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=50)
    due_date: Optional[date] = None
    status: Optional[str] = Field(default=None, pattern="^(pending|done)$")


class HomeworkTaskRead(HomeworkTaskBase):
    id: int
    user_id: str
    conversation_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

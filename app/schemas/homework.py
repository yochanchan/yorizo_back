from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import HomeworkStatus


class HomeworkTaskBase(BaseModel):
    title: str = Field(..., max_length=255)
    detail: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=50)
    due_date: Optional[date] = None
    timeframe: Optional[str] = Field(default=None, max_length=100)
    status: Optional[HomeworkStatus] = None


class HomeworkTaskCreate(HomeworkTaskBase):
    user_id: str
    conversation_id: str
    status: HomeworkStatus = HomeworkStatus.PENDING


class HomeworkTaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)
    detail: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=50)
    due_date: Optional[date] = None
    timeframe: Optional[str] = Field(default=None, max_length=100)
    status: Optional[HomeworkStatus] = None


class HomeworkTaskRead(HomeworkTaskBase):
    id: int
    user_id: str
    conversation_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    status: HomeworkStatus | None = HomeworkStatus.PENDING

    model_config = ConfigDict(from_attributes=True)

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict
from app.models.enums import ConversationStatus, HomeworkStatus
from app.schemas.homework import HomeworkTaskRead


class ConversationSummary(BaseModel):
    id: str
    title: str
    date: str
    category: Optional[str] = None
    status: Optional[ConversationStatus] = ConversationStatus.IN_PROGRESS

    model_config = ConfigDict(from_attributes=True)


class ConversationListResponse(BaseModel):
    conversations: List[ConversationSummary]


class ConversationMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationDetail(BaseModel):
    id: str
    title: str
    started_at: datetime | None = None
    category: Optional[str] = None
    status: Optional[ConversationStatus] = ConversationStatus.IN_PROGRESS
    step: Optional[int] = None
    messages: List[ConversationMessage]

    model_config = ConfigDict(from_attributes=True)


class ConsultationMemoResponse(BaseModel):
    current_points: List[str]
    important_points: List[str]
    updated_at: datetime


class ConsultationMemoRequest(BaseModel):
    regenerate: bool = False


class ConversationCreate(BaseModel):
    user_id: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    status: Optional[ConversationStatus] = ConversationStatus.IN_PROGRESS
    step: Optional[int] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    status: Optional[ConversationStatus] = None
    step: Optional[int] = None


class ConversationReport(BaseModel):
    id: str
    title: str
    date: date
    summary: List[str]
    key_topics: List[str]
    homework: List[HomeworkTaskRead]
    self_actions: List[HomeworkTaskRead] = []

    model_config = ConfigDict(from_attributes=True)


class ReportHomework(BaseModel):
    id: int | None = None
    title: str
    detail: str | None = None
    timeframe: str | None = None
    status: HomeworkStatus | None = None

    model_config = ConfigDict(from_attributes=True)


class ReportResponse(BaseModel):
    id: str
    title: str
    category: Optional[str] = None
    created_at: datetime
    summary: List[str]
    financial_analysis: List[str]
    strengths: List[str]
    weaknesses: List[str]
    homework: List[ReportHomework]

    model_config = ConfigDict(from_attributes=True)

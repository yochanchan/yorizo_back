from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.enums import BookingStatus


class BookingListItem(BaseModel):
    id: str
    expert_id: str
    expert_name: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    conversation_id: Optional[str] = None
    channel: str
    status: BookingStatus
    date: date
    time_slot: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    note: Optional[str] = None
    meeting_url: Optional[str] = None
    line_contact: Optional[str] = None
    created_at: datetime


class BookingListResponse(BaseModel):
    bookings: list[BookingListItem]


class BookingDetail(BaseModel):
    id: str
    expert_id: str
    expert_name: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    conversation_id: Optional[str] = None
    channel: str
    status: BookingStatus
    date: date
    time_slot: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    note: Optional[str] = None
    meeting_url: Optional[str] = None
    line_contact: Optional[str] = None
    created_at: datetime


class BookingUpdateRequest(BaseModel):
    status: Optional[BookingStatus] = Field(None, description="pending/confirmed/done/cancelled")
    note: Optional[str] = None
    conversation_id: Optional[str] = None
    meeting_url: Optional[str] = Field(None, description="Online meeting URL (Zoom/Teams etc.)")
    line_contact: Optional[str] = Field(None, description="LINE contact URL/ID")

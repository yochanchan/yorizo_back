from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class ExpertResponse(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str] = None
    title: Optional[str] = None
    organization: Optional[str] = None
    tags: List[str] = []
    rating: float
    review_count: int
    location_prefecture: Optional[str] = None
    description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AvailabilitySlot(BaseModel):
    date: date
    slots: List[str]
    booked_slots: List[str]
    available_count: int


class ExpertAvailabilityResponse(BaseModel):
    expert_id: str
    availability: List[AvailabilitySlot]


class ConsultationBookingRequest(BaseModel):
    expert_id: str
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    date: date
    time_slot: str
    channel: Literal["online", "in-person"]
    name: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    note: Optional[str] = None
    meeting_url: Optional[str] = None
    line_contact: Optional[str] = None


class ConsultationBookingResponse(BaseModel):
    booking_id: str
    expert_id: str
    conversation_id: Optional[str] = None
    date: date
    time_slot: str
    channel: str
    meeting_url: Optional[str] = None
    line_contact: Optional[str] = None
    message: str

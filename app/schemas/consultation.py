from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class ConsultationBookingListItem(BaseModel):
  id: str
  date: date
  time_slot: str
  channel: str
  status: str
  expert_name: Optional[str] = None


class ConsultationBookingListResponse(BaseModel):
  bookings: List[ConsultationBookingListItem]


class ConsultationMemoListItem(BaseModel):
  conversation_id: str
  created_at: datetime
  current_point_preview: str
  important_point_preview: str


class ConsultationMemoListResponse(BaseModel):
  memos: List[ConsultationMemoListItem]

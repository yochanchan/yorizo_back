from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE, default_uuid, utcnow
from app.models.enums import BookingStatus


class Expert(Base):
    __tablename__ = "experts"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    name = Column(String(255), nullable=False)
    avatar_url = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)
    organization = Column(String(255), nullable=True)
    tags = Column(Text, nullable=True)
    rating = Column(Float, default=4.5)
    review_count = Column(Integer, default=0)
    location_prefecture = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)

    availabilities = relationship("ExpertAvailability", back_populates="expert", cascade="all, delete-orphan")
    bookings = relationship("ConsultationBooking", back_populates="expert", cascade="all, delete-orphan")


class ExpertAvailability(Base):
    __tablename__ = "expert_availabilities"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    expert_id = Column(GUID_TYPE, ForeignKey("experts.id"), nullable=False)
    date = Column(Date, nullable=False)
    slots_json = Column(Text, nullable=False)

    expert = relationship("Expert", back_populates="availabilities")


class ConsultationBooking(Base):
    __tablename__ = "consultation_bookings"
    __table_args__ = (
        UniqueConstraint("expert_id", "date", "time_slot", name="uq_consultation_booking_slot"),
    )

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    expert_id = Column(GUID_TYPE, ForeignKey("experts.id"), nullable=False)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=True)
    conversation_id = Column(GUID_TYPE, ForeignKey("conversations.id"), nullable=True)
    date = Column(Date, nullable=False)
    time_slot = Column(String(50), nullable=False)
    channel = Column(String(20), default="online")
    status = Column(String(20), nullable=False, default=BookingStatus.PENDING.value)
    name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)
    meeting_url = Column(String(512), nullable=True)
    line_contact = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    expert = relationship("Expert", back_populates="bookings")
    user = relationship("User", back_populates="bookings")
    conversation = relationship("Conversation")


__all__ = ["Expert", "ExpertAvailability", "ConsultationBooking"]

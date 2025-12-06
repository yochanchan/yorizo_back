from __future__ import annotations

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE, default_uuid, utcnow


class User(Base):
    __tablename__ = "users"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    external_id = Column(String(255), nullable=True)
    nickname = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    bookings = relationship("ConsultationBooking", back_populates="user", cascade="all, delete-orphan")
    company_profile = relationship("CompanyProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    homework_tasks = relationship("HomeworkTask", back_populates="user", cascade="all, delete-orphan")
    rag_documents = relationship("RAGDocument", back_populates="user", cascade="all, delete-orphan")
    companies = relationship("Company", back_populates="owner", cascade="all, delete-orphan")


__all__ = ["User"]

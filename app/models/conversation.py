from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE, default_uuid, utcnow
from app.models.enums import ConversationStatus


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=True)
    title = Column(String(255), nullable=True)
    started_at = Column(DateTime, default=utcnow)
    ended_at = Column(DateTime, nullable=True)
    main_concern = Column(Text, nullable=True)
    channel = Column(String(50), default="chat")
    category = Column(String(32), nullable=True)
    status = Column(String(32), default=ConversationStatus.IN_PROGRESS.value)
    step = Column(Integer, nullable=True)

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    memo = relationship("ConsultationMemo", back_populates="conversation", uselist=False, cascade="all, delete-orphan")
    homework_tasks = relationship("HomeworkTask", back_populates="conversation", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    conversation_id = Column(GUID_TYPE, ForeignKey("conversations.id"), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class ConsultationMemo(Base):
    __tablename__ = "consultation_memos"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    conversation_id = Column(GUID_TYPE, ForeignKey("conversations.id"), nullable=False, unique=True)
    current_points = Column(Text, nullable=True)
    important_points = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    conversation = relationship("Conversation", back_populates="memo")


__all__ = ["Conversation", "Message", "ConsultationMemo"]

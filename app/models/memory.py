from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE, default_uuid, utcnow
from app.models.enums import HomeworkStatus


class Memory(Base):
    __tablename__ = "memories"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=False)
    current_concerns = Column(Text, nullable=True)
    important_points = Column(Text, nullable=True)
    remembered_facts = Column(Text, nullable=True)
    last_updated_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="memories")


class HomeworkTask(Base):
    __tablename__ = "homework_tasks"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(GUID_TYPE, ForeignKey("conversations.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    detail = Column(Text, nullable=True)
    category = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default=HomeworkStatus.PENDING.value)
    due_date = Column(Date, nullable=True)
    timeframe = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="homework_tasks")
    conversation = relationship("Conversation", back_populates="homework_tasks")


__all__ = ["Memory", "HomeworkTask"]

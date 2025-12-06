from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE, default_uuid, utcnow


class Document(Base):
    __tablename__ = "documents"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=True)
    company_id = Column(GUID_TYPE, nullable=True)
    conversation_id = Column(GUID_TYPE, ForeignKey("conversations.id"), nullable=True)
    filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=True)
    size_bytes = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, default=utcnow)
    content_text = Column(Text, nullable=True)
    doc_type = Column(String(50), nullable=True)
    period_label = Column(String(50), nullable=True)
    storage_path = Column(String(500), nullable=False)
    ingested = Column(Boolean, default=False)

    user = relationship("User", back_populates="documents")
    conversation = relationship("Conversation", back_populates="documents")


class RAGDocument(Base):
    __tablename__ = "rag_documents"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(512), nullable=False)
    source_type = Column(String(50), nullable=False, default="manual")
    source_id = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=True)
    embedding = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="rag_documents")


__all__ = ["Document", "RAGDocument"]

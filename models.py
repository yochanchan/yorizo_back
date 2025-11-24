from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    func,
)
from sqlalchemy.orm import relationship

from database import Base


def default_uuid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=default_uuid)
    external_id = Column(String, nullable=True)
    nickname = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    bookings = relationship("ConsultationBooking", back_populates="user", cascade="all, delete-orphan")
    company_profile = relationship("CompanyProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    homework_tasks = relationship("HomeworkTask", back_populates="user", cascade="all, delete-orphan")
    rag_documents = relationship("RAGDocument", back_populates="user", cascade="all, delete-orphan")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=default_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    title = Column(String, nullable=True)
    started_at = Column(DateTime, default=utcnow)
    ended_at = Column(DateTime, nullable=True)
    main_concern = Column(Text, nullable=True)
    channel = Column(String, default="chat")

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    memo = relationship("ConsultationMemo", back_populates="conversation", uselist=False, cascade="all, delete-orphan")
    homework_tasks = relationship("HomeworkTask", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=default_uuid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    conversation = relationship("Conversation", back_populates="messages")


class Memory(Base):
    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=default_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    current_concerns = Column(Text, nullable=True)
    important_points = Column(Text, nullable=True)
    remembered_facts = Column(Text, nullable=True)
    last_updated_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="memories")


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id = Column(String, primary_key=True, default=default_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, unique=True)
    company_name = Column(String, nullable=True)
    industry = Column(String, nullable=True)
    employees_range = Column(String, nullable=True)
    annual_sales_range = Column(String, nullable=True)
    location_prefecture = Column(String, nullable=True)
    years_in_business = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="company_profile")


class ConsultationMemo(Base):
    __tablename__ = "consultation_memos"

    id = Column(String, primary_key=True, default=default_uuid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, unique=True)
    current_points = Column(Text, nullable=True)
    important_points = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    conversation = relationship("Conversation", back_populates="memo")


class Expert(Base):
    __tablename__ = "experts"

    id = Column(String, primary_key=True, default=default_uuid)
    name = Column(String, nullable=False)
    avatar_url = Column(String, nullable=True)
    title = Column(String, nullable=True)
    organization = Column(String, nullable=True)
    tags = Column(Text, nullable=True)
    rating = Column(Float, default=4.5)
    review_count = Column(Integer, default=0)
    location_prefecture = Column(String, nullable=True)
    description = Column(Text, nullable=True)

    availabilities = relationship("ExpertAvailability", back_populates="expert", cascade="all, delete-orphan")
    bookings = relationship("ConsultationBooking", back_populates="expert", cascade="all, delete-orphan")


class ExpertAvailability(Base):
    __tablename__ = "expert_availabilities"

    id = Column(String, primary_key=True, default=default_uuid)
    expert_id = Column(String, ForeignKey("experts.id"), nullable=False)
    date = Column(Date, nullable=False)
    slots_json = Column(Text, nullable=False)

    expert = relationship("Expert", back_populates="availabilities")


class ConsultationBooking(Base):
    __tablename__ = "consultation_bookings"

    id = Column(String, primary_key=True, default=default_uuid)
    expert_id = Column(String, ForeignKey("experts.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    date = Column(Date, nullable=False)
    time_slot = Column(String, nullable=False)
    channel = Column(String, default="online")
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    expert = relationship("Expert", back_populates="bookings")
    user = relationship("User", back_populates="bookings")


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=default_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    filename = Column(String, nullable=False)
    mime_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, default=utcnow)
    content_text = Column(Text, nullable=True)

    user = relationship("User", back_populates="documents")


class RAGDocument(Base):
    __tablename__ = "rag_documents"

    id = Column(BigInteger, primary_key=True, autoincrement=True, index=True)
    user_id = Column(String(255), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(512), nullable=False)
    source_type = Column(String(50), nullable=False, default="manual")
    source_id = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=True)
    embedding = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="rag_documents")


class HomeworkTask(Base):
    __tablename__ = "homework_tasks"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    detail = Column(Text, nullable=True)
    category = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    due_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="homework_tasks")
    conversation = relationship("Conversation", back_populates="homework_tasks")

"""
Compatibility shim for legacy `models` imports.
Domain models have moved to `app.models.*`.
"""
from app.models import (  # noqa: F401,F403
    BookingStatus,
    ConsultationBooking,
    ConsultationMemo,
    Conversation,
    ConversationStatus,
    Document,
    Expert,
    ExpertAvailability,
    FinancialStatement,
    GUID_LENGTH,
    GUID_TYPE,
    HomeworkStatus,
    HomeworkTask,
    Memory,
    Message,
    RAGDocument,
    User,
    Company,
    CompanyProfile,
    default_uuid,
    utcnow,
)
from database import Base  # noqa: F401

__all__ = [
    "Base",
    "GUID_TYPE",
    "GUID_LENGTH",
    "default_uuid",
    "utcnow",
    "BookingStatus",
    "ConversationStatus",
    "HomeworkStatus",
    "User",
    "Conversation",
    "Message",
    "ConsultationMemo",
    "Memory",
    "HomeworkTask",
    "Company",
    "CompanyProfile",
    "Document",
    "FinancialStatement",
    "RAGDocument",
    "Expert",
    "ExpertAvailability",
    "ConsultationBooking",
]

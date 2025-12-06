from __future__ import annotations

from enum import Enum


class ConversationStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class HomeworkStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"


class BookingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DONE = "done"
    CANCELLED = "cancelled"


__all__ = ["ConversationStatus", "HomeworkStatus", "BookingStatus"]

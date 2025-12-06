from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import String

# NOTE: MySQL requires FK columns to match referenced column type exactly.
GUID_LENGTH = 36
GUID_TYPE = String(GUID_LENGTH)


def default_uuid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.utcnow()


__all__ = ["GUID_TYPE", "GUID_LENGTH", "default_uuid", "utcnow"]

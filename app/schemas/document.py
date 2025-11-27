from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    uploaded_at: datetime
    summary: str
    storage_path: str
    ingested: bool


class DocumentItem(BaseModel):
    id: str
    filename: str
    uploaded_at: datetime
    size_bytes: int
    mime_type: Optional[str] = None
    content_type: Optional[str] = None
    company_id: Optional[str] = None
    conversation_id: Optional[str] = None
    doc_type: Optional[str] = None
    period_label: Optional[str] = None
    storage_path: str
    ingested: bool
    content_text: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DocumentListResponse(BaseModel):
    documents: List[DocumentItem]

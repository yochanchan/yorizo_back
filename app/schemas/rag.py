from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RagChatRequest(BaseModel):
    question: str
    history: List[str] = Field(default_factory=list)
    user_id: Optional[str] = None


class RagChatResponse(BaseModel):
    answer: str
    contexts: List[str]


class RagDocumentBase(BaseModel):
    title: str = Field(..., max_length=512)
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    user_id: Optional[str] = None
    source_type: str = Field(default="manual", max_length=50)
    source_id: Optional[str] = Field(default=None, max_length=255)


class RagDocumentCreate(RagDocumentBase):
    pass


class RagDocumentResponse(RagDocumentBase):
    id: int
    text: str = Field(alias="content")
    metadata: Dict[str, Any] = Field(default_factory=dict, alias="metadata_json")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RagSimilarDocument(BaseModel):
    id: int
    title: str
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    score: float


class RagQueryRequest(BaseModel):
    question: str
    top_k: int = Field(default=5, ge=1, le=20)
    user_id: Optional[str] = None


class RagQueryResponse(BaseModel):
    matches: List[RagSimilarDocument]

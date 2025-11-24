from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RagChatMessage(BaseModel):
    role: str
    content: str


class RagChatRequest(BaseModel):
    messages: List[RagChatMessage] = Field(default_factory=list, description="対話の履歴（user/assistant）")
    question: Optional[str] = Field(default=None, description="messages が無い場合の単発質問")
    history: List[str] = Field(default_factory=list, description="後方互換用: 過去 user 発話のみを渡す場合")
    top_k: int = Field(default=5, ge=1, le=20)
    user_id: Optional[str] = None
    company_id: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class RagChatResponse(BaseModel):
    answer: str
    contexts: List[str]
    citations: List[int] = Field(default_factory=list)


class RagDocumentBase(BaseModel):
    title: str = Field(..., max_length=512)
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    user_id: Optional[str] = None
    company_id: Optional[str] = None
    source_type: str = Field(default="manual", max_length=50)
    source_id: Optional[str] = Field(default=None, max_length=255)


class RagDocumentCreate(RagDocumentBase):
    pass


class RagDocumentCreateRequest(BaseModel):
    user_id: Optional[str] = None
    company_id: Optional[str] = None
    documents: List[RagDocumentCreate]


class RagDocumentResponse(RagDocumentBase):
    id: int
    text: str = Field(alias="content")
    metadata: Dict[str, Any] = Field(default_factory=dict, alias="metadata_json")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RagDocumentCreateResponse(BaseModel):
    documents: List[RagDocumentResponse]


class RagSimilarDocument(BaseModel):
    id: int
    title: str
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    score: float


class RagQueryRequest(BaseModel):
    query: str = Field(..., alias="question")
    top_k: int = Field(default=5, ge=1, le=20)
    user_id: Optional[str] = None
    company_id: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class RagQueryResponse(BaseModel):
    matches: List[RagSimilarDocument]

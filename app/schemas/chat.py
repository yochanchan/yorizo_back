from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ChatOption(BaseModel):
    id: str = Field(..., description="Internal ID for the option")
    label: str = Field(..., description="Text shown in the UI")
    value: Optional[str] = Field(None, description="Text that is treated as the user answer")


class GuidedUserSelection(BaseModel):
    type: Literal["choice", "free_text"]
    id: Optional[str] = Field(None, description="Internal option key when type is choice")
    label: Optional[str] = Field(None, description="Display label (Japanese) for the choice")
    text: Optional[str] = Field(None, description="Free text when type is free_text")


class ChatTurnRequest(BaseModel):
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    company_id: Optional[str] = None
    selection: Optional[GuidedUserSelection] = None
    message: Optional[str] = Field(None, description="Free text input from the user (legacy)")
    selected_option_id: Optional[str] = Field(None, description="Option chosen by the user (legacy)")
    category: Optional[str] = Field(None, description="High-level topic: sales/cash/hr/ops/other")


class ChatTurnResponse(BaseModel):
    conversation_id: str
    reply: str
    question: str
    options: List[ChatOption] = Field(default_factory=list)
    allow_free_text: bool = True
    step: int = 1
    done: bool = False

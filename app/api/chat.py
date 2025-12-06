import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.schemas.chat import ChatTurnRequest, ChatTurnResponse
from app.services.chat_flow import run_guided_chat
from database import get_db

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/guided", response_model=ChatTurnResponse)
async def guided_chat_turn(payload: ChatTurnRequest, db: Session = Depends(get_db)) -> ChatTurnResponse:
    return await run_guided_chat(payload, db)


@router.post("", response_model=ChatTurnResponse)
async def chat_turn(payload: ChatTurnRequest, db: Session = Depends(get_db)) -> ChatTurnResponse:
    """
    従来のエントリポイント。ガイド付きフローにフォワードする。
    """
    return await run_guided_chat(payload, db)

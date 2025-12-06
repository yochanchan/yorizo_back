from datetime import datetime
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.openai_client import generate_consultation_memo
from app.models import ConsultationMemo, Conversation, HomeworkTask, Message, User
from app.models.enums import ConversationStatus
from app.schemas.conversation import (
    ConsultationMemoResponse,
    ConversationDetail,
    ConversationListResponse,
    ConversationMessage,
    ConversationReport,
    ConversationSummary,
)
from database import get_db

router = APIRouter()


def _ensure_user(db: Session, user_id: str | None) -> User | None:
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, nickname="ゲスト")
        db.add(user)
        db.commit()
    return user


def _conversation_title(conv: Conversation) -> str:
    if conv.main_concern:
        return conv.main_concern[:20]
    if conv.messages:
        for msg in conv.messages:
            if msg.role == "user":
                return msg.content[:20]
    return conv.title or "相談"


def _parse_points(raw: str | None) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data][:10]
    except Exception:
        pass
    return [raw]


async def _build_and_save_memo(db: Session, conversation: Conversation) -> ConsultationMemo:
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .limit(50)
        .all()
    )
    payload = [{"role": m.role, "content": m.content} for m in messages]
    current_points, important_points = await generate_consultation_memo(payload)

    memo = conversation.memo
    now = datetime.utcnow()
    if memo is None:
        memo = ConsultationMemo(
            conversation_id=conversation.id,
            current_points=json.dumps(current_points, ensure_ascii=False),
            important_points=json.dumps(important_points, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        db.add(memo)
    else:
        memo.current_points = json.dumps(current_points, ensure_ascii=False)
        memo.important_points = json.dumps(important_points, ensure_ascii=False)
        memo.updated_at = now

    db.commit()
    db.refresh(memo)
    return memo


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    user_id: str = Query("demo-user"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ConversationListResponse:
    _ensure_user(db, user_id)
    query = db.query(Conversation).order_by(Conversation.started_at.desc())
    if user_id:
        query = query.filter(Conversation.user_id == user_id)
    conversations = query.offset(offset).limit(limit).all()

    summaries: List[ConversationSummary] = []
    for conv in conversations:
        started = conv.started_at or datetime.utcnow()
        summaries.append(
            ConversationSummary(
                id=conv.id,
                title=_conversation_title(conv),
                date=started.date().isoformat(),
                category=conv.category,
                status=conv.status or ConversationStatus.IN_PROGRESS.value,
            )
        )
    return ConversationListResponse(conversations=summaries)


@router.get("/conversations/{conversation_id}/memo", response_model=ConsultationMemoResponse)
async def get_consultation_memo(conversation_id: str, db: Session = Depends(get_db)) -> ConsultationMemoResponse:
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    memo = conversation.memo
    if memo is None:
        memo = await _build_and_save_memo(db, conversation)

    return ConsultationMemoResponse(
        current_points=_parse_points(memo.current_points),
        important_points=_parse_points(memo.important_points),
        updated_at=memo.updated_at or memo.created_at or datetime.utcnow(),
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation_detail(conversation_id: str, db: Session = Depends(get_db)) -> ConversationDetail:
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    title = _conversation_title(conv)
    return ConversationDetail(
        id=conv.id,
        title=title,
        started_at=conv.started_at,
        category=conv.category,
        status=conv.status or ConversationStatus.IN_PROGRESS.value,
        step=conv.step,
        messages=[
            ConversationMessage(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


@router.post("/conversations/{conversation_id}/memo/refresh", response_model=ConsultationMemoResponse)
async def refresh_consultation_memo(conversation_id: str, db: Session = Depends(get_db)) -> ConsultationMemoResponse:
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    memo = await _build_and_save_memo(db, conversation)

    return ConsultationMemoResponse(
        current_points=_parse_points(memo.current_points),
        important_points=_parse_points(memo.important_points),
        updated_at=memo.updated_at or memo.created_at or datetime.utcnow(),
    )


@router.get("/conversations/{conversation_id}/report", response_model=ConversationReport)
async def get_conversation_report(conversation_id: str, db: Session = Depends(get_db)) -> ConversationReport:
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    memo = conv.memo
    if memo is None:
        memo = await _build_and_save_memo(db, conv)

    summary = _parse_points(memo.current_points)
    key_topics = _parse_points(memo.important_points)

    homework_tasks = (
        db.query(HomeworkTask)
        .filter(HomeworkTask.conversation_id == conv.id)
        .order_by(HomeworkTask.created_at.asc())
        .all()
    )

    title = _conversation_title(conv)
    return ConversationReport(
        id=conv.id,
        title=title,
        date=(conv.started_at or datetime.utcnow()).date(),
        summary=summary,
        key_topics=key_topics,
        homework=homework_tasks,
        self_actions=homework_tasks,
    )

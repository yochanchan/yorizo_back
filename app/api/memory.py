import json
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.schemas.homework import HomeworkTaskRead
from app.models.enums import HomeworkStatus
from database import get_db
from app.models import CompanyProfile, Conversation, HomeworkTask, Memory, Message, User

CHOICE_ID_PATTERN = re.compile(r"^\[choice_id:[^\]]+\]\s*")


class PastConversation(BaseModel):
    id: str
    title: str
    date: str


class CompanyProfileSummary(BaseModel):
    company_name: Optional[str] = None
    industry: Optional[str] = None
    employees_range: Optional[str] = None
    annual_sales_range: Optional[str] = None
    location_prefecture: Optional[str] = None


class MemorySummary(BaseModel):
    current_summary: List[str]
    key_problems: List[str]
    homework: List[HomeworkTaskRead]
    expert_points: List[str]
    company_profile: Optional[CompanyProfileSummary] = None


class MemoryResponse(BaseModel):
    current_concerns: List[str]
    important_points_for_expert: List[str]
    nickname: str
    remembered_facts: List[str]
    past_conversations: List[PastConversation]
    summary: MemorySummary


router = APIRouter()
STATUS_MAP = {
    "未着手": HomeworkStatus.PENDING.value,
    "対応中": HomeworkStatus.PENDING.value,
    "進行中": HomeworkStatus.PENDING.value,
    "完了": HomeworkStatus.DONE.value,
    "完遂": HomeworkStatus.DONE.value,
    "pending": HomeworkStatus.PENDING.value,
    "in_progress": HomeworkStatus.PENDING.value,
    "done": HomeworkStatus.DONE.value,
    None: HomeworkStatus.PENDING.value,
}


def _ensure_user(db: Session, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, nickname="ゲスト")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _json_to_list(raw: Optional[str], fallback: List[str]) -> List[str]:
    if not raw:
        return fallback
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except Exception:
        pass
    return fallback


def _clean_title(value: Optional[str]) -> str:
    if not value:
        return ""
    return CHOICE_ID_PATTERN.sub("", value).strip() or "相談メモ"


def _dedupe(items: List[str], limit: int) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        text = (item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
        if len(ordered) >= limit:
            break
    return ordered


def _get_target_conversation(
    db: Session, user_id: str, conversation_id: Optional[str]
) -> tuple[Optional[Conversation], List[Message]]:
    conversation: Optional[Conversation] = None
    messages: List[Message] = []

    if conversation_id:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id)
            .first()
        )
        if conversation and conversation.user_id and conversation.user_id != user_id:
            conversation = None

    if not conversation:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(Conversation.started_at.desc())
            .first()
        )

    if conversation:
        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.asc())
            .all()
        )
    return conversation, messages


def _build_current_summary(
    conversation: Optional[Conversation],
    messages: List[Message],
    current_concerns: List[str],
) -> List[str]:
    bullets: List[str] = []
    if conversation:
        title = _clean_title(conversation.title or conversation.main_concern or "")
        if title:
            bullets.append(f"最新の相談テーマ: {title}")
        if conversation.category:
            bullets.append(f"関心のジャンル: {conversation.category}")

    user_comments = [
        CHOICE_ID_PATTERN.sub("", msg.content).strip()
        for msg in messages
        if msg.role == "user" and msg.content
    ]
    for text in user_comments[-2:]:
        if text:
            bullets.append(f"ユーザーの声: {text}")

    if not bullets:
        bullets.extend(current_concerns)

    return _dedupe(bullets, 5)


def _build_key_problems(
    conversation: Optional[Conversation],
    current_concerns: List[str],
) -> List[str]:
    problems: List[str] = []
    if conversation and conversation.main_concern:
        problems.append(_clean_title(conversation.main_concern))
    problems.extend(current_concerns)
    if conversation and conversation.category:
        problems.append(f"{conversation.category}に関する課題が継続中")
    if not problems:
        problems.append("具体的な課題はこれから整理していきましょう。")
    return _dedupe(problems, 5)


def _build_expert_points(
    important_points: List[str],
    remembered_facts: List[str],
    conversation: Optional[Conversation],
    homework: List[HomeworkTask],
) -> List[str]:
    points: List[str] = []
    points.extend(important_points)
    points.extend(remembered_facts[:2])
    if conversation and conversation.step:
        points.append(f"ガイド付きチャットはステップ{conversation.step}まで回答済みです。")
    if homework:
        points.append(f"未完了の宿題が{len(homework)}件あります。")
    if not points:
        points.append("現時点で特筆すべきポイントはありません。")
    return _dedupe(points, 4)


def _build_company_profile_summary(profile: Optional[CompanyProfile]) -> Optional[CompanyProfileSummary]:
    if not profile:
        return None
    return CompanyProfileSummary(
        company_name=profile.company_name,
        industry=profile.industry,
        employees_range=profile.employees_range,
        annual_sales_range=profile.annual_sales_range,
        location_prefecture=profile.location_prefecture,
    )


def _generate_homework_summary(tasks: List[HomeworkTask]) -> List[HomeworkTaskRead]:
    def _normalize_status(raw: Optional[str]) -> str:
        if raw is None:
            return HomeworkStatus.PENDING.value
        return STATUS_MAP.get(raw.strip(), HomeworkStatus.PENDING.value)

    converted: List[HomeworkTaskRead] = []
    for task in tasks:
        converted.append(
            HomeworkTaskRead(
                id=task.id,
                user_id=task.user_id,
                conversation_id=task.conversation_id,
                title=task.title,
                detail=task.detail,
                category=task.category,
                due_date=task.due_date,
                timeframe=getattr(task, "timeframe", None),
                status=_normalize_status(task.status),
                created_at=task.created_at,
                updated_at=task.updated_at,
                completed_at=task.completed_at,
            )
        )
    return converted


def _build_memory_summary(
    conversation: Optional[Conversation],
    messages: List[Message],
    current_concerns: List[str],
    important_points: List[str],
    remembered_facts: List[str],
    homework_tasks: List[HomeworkTask],
    profile: Optional[CompanyProfile],
) -> MemorySummary:
    current_summary = _build_current_summary(conversation, messages, current_concerns)
    key_problems = _build_key_problems(conversation, current_concerns)
    expert_points = _build_expert_points(important_points, remembered_facts, conversation, homework_tasks)
    homework = _generate_homework_summary(homework_tasks)
    company_profile = _build_company_profile_summary(profile)

    return MemorySummary(
        current_summary=current_summary,
        key_problems=key_problems,
        homework=homework,
        expert_points=expert_points,
        company_profile=company_profile,
    )


def _prepare_memory_response(
    db: Session, user_id: str, conversation_id: Optional[str]
) -> MemoryResponse:
    user = _ensure_user(db, user_id)
    memory = db.query(Memory).filter(Memory.user_id == user.id).first()

    if not memory:
        memory = Memory(
            user_id=user.id,
            current_concerns=json.dumps(["原材料費の高騰で利益率が下がっている"], ensure_ascii=False),
            important_points=json.dumps(["直近1年の粗利率の推移を専門家と確認したい"], ensure_ascii=False),
            remembered_facts=json.dumps(["福岡市で飲食店を経営している"], ensure_ascii=False),
            last_updated_at=datetime.utcnow(),
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)

    past = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.started_at.desc())
        .limit(10)
        .all()
    )
    past_conversations = [
        PastConversation(
            id=conv.id,
            title=_clean_title(conv.title or conv.main_concern or "相談"),
            date=(conv.started_at or datetime.utcnow()).date().isoformat(),
        )
        for conv in past
    ]

    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == user.id)
        .first()
    )

    conversation, messages = _get_target_conversation(db, user.id, conversation_id)

    current_concerns = _json_to_list(memory.current_concerns, [])
    important_points = _json_to_list(memory.important_points, [])
    remembered_facts = _json_to_list(memory.remembered_facts, [])

    homework_tasks = (
        db.query(HomeworkTask)
        .filter(HomeworkTask.user_id == user.id, HomeworkTask.status != HomeworkStatus.DONE.value)
        .order_by(HomeworkTask.created_at.desc())
        .limit(10)
        .all()
    )

    summary = _build_memory_summary(
        conversation,
        messages,
        current_concerns,
        important_points,
        remembered_facts,
        homework_tasks,
        profile,
    )

    return MemoryResponse(
        current_concerns=current_concerns,
        important_points_for_expert=important_points,
        nickname=user.nickname or "ゲストさま",
        remembered_facts=remembered_facts,
        past_conversations=past_conversations,
        summary=summary,
    )


@router.get("/memory/{user_id}", response_model=MemoryResponse)
async def get_memory(
    user_id: str,
    conversation_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> MemoryResponse:
    return _prepare_memory_response(db, user_id, conversation_id)


@router.get("/memory", response_model=MemoryResponse)
async def get_memory_query(
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> MemoryResponse:
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    return _prepare_memory_response(db, user_id, conversation_id)

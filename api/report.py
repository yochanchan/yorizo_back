import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
from app.schemas.reports import LocalBenchmark
from database import get_db
from models import CompanyProfile, Conversation, Document, HomeworkTask, Message
from services import rag as rag_service
from services.reports import build_local_benchmark

router = APIRouter(prefix="/report", tags=["report"])
logger = logging.getLogger(__name__)


class ReportHomework(BaseModel):
    id: Optional[int] = None
    title: str
    detail: Optional[str] = None
    timeframe: Optional[str] = None
    status: str


class ReportResponse(BaseModel):
    id: str
    title: str
    category: Optional[str] = None
    created_at: Optional[datetime] = None
    summary: List[str]
    financial_analysis: List[str]
    strengths: List[str]
    weaknesses: List[str]
    key_topics: List[str]
    for_expert: List[str]
    homework: List[ReportHomework]
    local_benchmark: Optional[LocalBenchmark] = None


class ReportEnvelope(BaseModel):
    exists: bool
    report: Optional[ReportResponse] = None


STATUS_MAP = {
    "未着手": "pending",
    "対応中": "in_progress",
    "進行中": "in_progress",
    "完了": "done",
    "完了済": "done",
    "pending": "pending",
    "in_progress": "in_progress",
    "done": "done",
    None: "pending",
}

CHOICE_ID_PATTERN = re.compile(r"\[choice_id:[^\]]+\]\s*")


def _normalize_status(raw: Optional[str]) -> str:
    return STATUS_MAP.get((raw or "").strip(), "pending")


def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return CHOICE_ID_PATTERN.sub("", text).strip()


def _build_conversation_text(messages: List[Message]) -> str:
    lines: List[str] = []
    for msg in messages[-40:]:
        content = _clean_text(msg.content)
        if not content:
            continue
        speaker = "ユーザー" if msg.role == "user" else "Yorizo"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def _build_documents_context(documents: List[Document]) -> str:
    if not documents:
        return ""
    snippets: List[str] = []
    for doc in documents[:5]:
        title = doc.title or doc.filename or "資料"
        meta_parts: List[str] = []
        if doc.doc_type:
            meta_parts.append(doc.doc_type)
        if doc.period_label:
            meta_parts.append(doc.period_label)
        meta = f"（{' / '.join(meta_parts)}）" if meta_parts else ""
        snippets.append(f"- {title}{meta}")
    return "\n".join(snippets)


def _normalize_section(items: Optional[List[Any]], fallback: List[str]) -> List[str]:
    normalized = [str(x).strip() for x in (items or []) if str(x).strip()]
    return normalized or fallback


def _fallback_sections(messages: List[Message]) -> Tuple[List[str], List[str], List[str]]:
    user_msgs = [_clean_text(m.content) for m in messages if m.role == "user" and _clean_text(m.content)]
    assistant_msgs = [
        _clean_text(m.content) for m in messages if m.role == "assistant" and _clean_text(m.content)
    ]
    summary = user_msgs[:3] or ["相談内容は整理中です。"]
    key_topics = user_msgs[3:6] or ["課題はこれから整理していきましょう。"]
    expert = assistant_msgs[-2:] or ["特別に伝えるポイントはまだ整理されていません。"]
    return summary, key_topics, expert


def _generate_consultation_sections(conversation_text: str, context_text: str) -> Optional[Dict[str, List[str]]]:
    if not conversation_text:
        return None

    system_prompt = (
        "あなたは中小企業診断士として振る舞う経営相談AI「Yorizo」です。"
        "会話履歴と参考資料を読み、相談メモの要約をJSONで返してください。"
        "必ず summary, key_topics, for_expert の3つのキーを含む配列で出力し、"
        "[choice_id:...] など内部タグは一切含めないでください。"
    )
    materials = context_text.strip() or "参考資料は特にありません。"
    user_prompt = (
        "以下はチャットの会話履歴です。内容を踏まえて、今回のポイントを整理してください。\n\n"
        f"# 会話履歴\n{conversation_text}\n\n"
        f"# 参考資料\n{materials}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw = chat_completion_json(messages=messages, max_tokens=900)
    data = json.loads(raw or "{}")
    return {
        "summary": _normalize_section(data.get("summary"), []),
        "key_topics": _normalize_section(data.get("key_topics"), []),
        "for_expert": _normalize_section(data.get("for_expert"), []),
    }


def _gather_rag_context(db: Session, conversation: Conversation, query: str) -> str:
    try:
        snippets = asyncio.run(
            rag_service.retrieve_context(
                db=db,
                user_id=conversation.user_id,
                company_id=None,
                query=query,
                top_k=6,
            )
        )
    except RuntimeError:
        snippets = []
    except Exception:
        logger.exception("Failed to retrieve RAG context for report")
        snippets = []
    return "\n".join(snippets)


@router.get("/{conversation_id}", response_model=ReportEnvelope)
def get_report(conversation_id: str, db: Session = Depends(get_db)) -> ReportEnvelope:
    try:
        conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation:
            return ReportEnvelope(exists=False, report=None)

        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .all()
        )

        status_order = case((HomeworkTask.status == "pending", 0), else_=1)
        due_date_nulls_last = case((HomeworkTask.due_date.is_(None), 1), else_=0)
        homework = (
            db.query(HomeworkTask)
            .filter(HomeworkTask.conversation_id == conversation_id)
            .order_by(
                status_order,
                due_date_nulls_last,
                HomeworkTask.due_date.asc(),
                HomeworkTask.created_at.asc(),
            )
            .all()
        )
        documents = (
            db.query(Document)
            .filter(
                (Document.conversation_id == conversation_id)
                | (Document.company_id == conversation.user_id)
            )
            .order_by(Document.uploaded_at.desc())
            .all()
        )

        profile: Optional[CompanyProfile] = None
        conversation_count_for_user = 0
        user_pending_homework_count = 0
        if conversation.user_id:
            profile = (
                db.query(CompanyProfile)
                .filter(CompanyProfile.user_id == conversation.user_id)
                .first()
            )
            conversation_count_for_user = (
                db.query(Conversation)
                .filter(Conversation.user_id == conversation.user_id)
                .count()
            )
            user_pending_homework_count = (
                db.query(HomeworkTask)
                .filter(
                    HomeworkTask.user_id == conversation.user_id,
                    HomeworkTask.status != "done",
                )
                .count()
            )
        else:
            conversation_count_for_user = len(messages)
            user_pending_homework_count = len([task for task in homework if task.status != "done"])

        conversation_text = _build_conversation_text(messages)
        docs_context = _build_documents_context(documents)
        rag_context = _gather_rag_context(db, conversation, conversation.main_concern or conversation.title or "")

        try:
            generated = _generate_consultation_sections(
                conversation_text,
                "\n".join(filter(None, [docs_context, rag_context])),
            )
            if not generated:
                raise ValueError("Empty sections.")
            summary_items = generated["summary"] or ["相談内容は整理中です。"]
            key_topics = generated["key_topics"] or ["課題はこれから整理していきましょう。"]
            expert_points = generated["for_expert"] or ["専門家に伝えるポイントは整理中です。"]
        except AzureNotConfiguredError:
            logger.warning("Azure OpenAI is not configured; falling back to heuristics.")
            summary_items, key_topics, expert_points = _fallback_sections(messages)
        except Exception:
            logger.exception("Report generation via Azure OpenAI failed; using fallback sections.")
            summary_items, key_topics, expert_points = _fallback_sections(messages)

        homework_items: List[ReportHomework] = [
            ReportHomework(
                id=task.id,
                title=task.title,
                detail=task.detail,
                timeframe=task.timeframe,
                status=_normalize_status(task.status),
            )
            for task in homework
        ]

        local_benchmark = None
        try:
            local_benchmark = build_local_benchmark(
                profile,
                conversation_count_for_user,
                len(documents),
                user_pending_homework_count,
            )
        except Exception:
            logger.debug("Failed to build local benchmark snapshot", exc_info=True)

        report = ReportResponse(
            id=str(conversation.id),
            title=conversation.title or "Yorizoの相談メモ",
            category=conversation.category,
            created_at=conversation.started_at or datetime.utcnow(),
            summary=summary_items,
            financial_analysis=[],
            strengths=[],
            weaknesses=[],
            key_topics=key_topics,
            for_expert=expert_points,
            homework=homework_items,
            local_benchmark=local_benchmark,
        )

        return ReportEnvelope(exists=True, report=report)

    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed to generate report for conversation_id=%s", conversation_id)
        raise HTTPException(status_code=500, detail="report_generation_failed") from exc

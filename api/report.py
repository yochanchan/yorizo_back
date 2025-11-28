import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
from database import get_db
from models import Conversation, Document, HomeworkTask, Message

# Included with prefix="/api" in main.py; use "/report" here to avoid double-prefix.
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
    homework: List[ReportHomework]


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


def _normalize_status(raw: Optional[str]) -> str:
    return STATUS_MAP.get((raw or "").strip(), "pending")


def _build_transcript(messages: List[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages[-32:])


def _build_homework_text(homework: List[HomeworkTask]) -> str:
    if not homework:
        return "宿題は登録されていません。"
    return "\n".join(
        f"- {task.title}（ステータス: {task.status or '未着手'}）" for task in homework[:10]
    )


def _build_documents_context(documents: List[Document]) -> str:
    if not documents:
        return "関連資料はありません。"
    return "\n".join(
        f"{doc.doc_type or 'document'}: {doc.filename}" for doc in documents[:5]
    )


def _heuristic_summary(messages: List[Message]) -> List[str]:
    return [msg.content for msg in messages if msg.role == "user"][:3]


def _generate_report(
    transcript: str, homework_text: str, docs_context: str
) -> Dict[str, Any]:
    system_prompt = """\
あなたは日本語で回答する中小企業診断AI「Yorizo」です。
以下の会話ログ・宿題・関連資料を読み、経営レポートをJSON形式で出力してください。
出力は必ず次のキーを含めてください: title, summary (配列), financial_analysis (配列), strengths (配列), weaknesses (配列), homework (配列)。
宿題の status は pending / in_progress / done のいずれかにしてください。
"""
    user_prompt = (
        f"会話ログ:\n{transcript}\n\n"
        f"宿題:\n{homework_text}\n\n"
        f"関連資料:\n{docs_context}\n"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    raw = chat_completion_json(messages=messages, max_tokens=900)
    return json.loads(raw or "{}")


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

        transcript = _build_transcript(messages)
        homework_text = _build_homework_text(homework)
        docs_context = _build_documents_context(documents)

        summary: List[str] = _heuristic_summary(messages)
        financial_analysis: List[str] = []
        strengths: List[str] = []
        weaknesses: List[str] = []
        generated: Dict[str, Any] = {}

        try:
            generated = _generate_report(transcript, homework_text, docs_context)
            if isinstance(generated, dict):
                summary = [str(x) for x in generated.get("summary") or summary if x][:4]
                financial_analysis = [str(x) for x in (generated.get("financial_analysis") or []) if x][:4]
                strengths = [str(x) for x in (generated.get("strengths") or []) if x][:3]
                weaknesses = [str(x) for x in (generated.get("weaknesses") or []) if x][:3]
        except AzureNotConfiguredError:
            logger.warning("Azure OpenAI is not configured; returning heuristic report.")
        except Exception:
            logger.exception("Report generation via Azure OpenAI failed")

        homework_items: List[ReportHomework] = []
        for task in homework:
            homework_items.append(
                ReportHomework(
                    id=task.id,
                    title=task.title,
                    detail=task.detail,
                    timeframe=task.timeframe,
                    status=_normalize_status(task.status),
                )
            )

        report = ReportResponse(
            id=str(conversation.id),
            title=conversation.title or "Yorizo????",
            category=conversation.category,
            created_at=conversation.started_at or datetime.utcnow(),
            summary=summary or ["?????????????????"],
            financial_analysis=financial_analysis,
            strengths=strengths,
            weaknesses=weaknesses,
            homework=homework_items,
        )

        return ReportEnvelope(exists=True, report=report)

    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - final safeguard
        logger.exception("Failed to generate report for conversation_id=%s", conversation_id)
        raise HTTPException(status_code=500, detail="report_generation_failed") from exc

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.openai_client import AzureNotConfiguredError
from database import get_db
from models import CompanyProfile, Conversation, Document, Message
from services import rag as rag_service
from services import reports as report_service

router = APIRouter(prefix="/report", tags=["report"])
logger = logging.getLogger(__name__)


def get_document_title(doc: Document) -> str:
    return (
        getattr(doc, "label", None)
        or getattr(doc, "original_filename", None)
        or getattr(doc, "filename", None)
        or "資料"
    )


class ScoreItem(BaseModel):
    key: str
    label: str
    raw: Optional[float] = None
    industry_avg: Optional[float] = None
    reason: str
    not_enough_data: bool = False


class FinanceSection(BaseModel):
    overview_comment: str
    scores: List[ScoreItem]


class ReportMeta(BaseModel):
    main_concern: Optional[str] = None
    period: str
    sources: List[str]


class ReportContext(BaseModel):
    meta: ReportMeta
    finance: Optional[FinanceSection] = None
    concerns: List[str]
    hints: List[str]


class ReportEnvelope(BaseModel):
    exists: bool
    report: Optional[ReportContext] = None


def _format_period(messages: List[Message], conversation: Conversation) -> str:
    if messages:
        start = messages[0].created_at or conversation.started_at or datetime.utcnow()
        end = messages[-1].created_at or conversation.started_at or datetime.utcnow()
    else:
        start = conversation.started_at or datetime.utcnow()
        end = conversation.started_at or datetime.utcnow()
    start_label = f"{start.year}年{start.month}月"
    end_label = f"{end.year}年{end.month}月"
    if start_label == end_label:
        return f"{start_label}のチャット相談"
    return f"{start_label}〜{end_label}に実施したチャット相談"


def _build_sources(profile: Optional[CompanyProfile], documents: List[Document], messages: List[Message]) -> List[str]:
    sources: List[str] = []
    if messages:
        sources.append("チャット相談の履歴")
    if profile:
        sources.append("会社プロフィールの登録情報")
    for doc in documents:
        title = get_document_title(doc)
        label = "アップロードされた資料"
        if doc.doc_type == "financial_statement":
            label = "アップロードされた決算書"
        elif doc.doc_type == "trial_balance":
            label = "アップロードされた試算表"
        elif doc.doc_type:
            label = f"アップロードされた{doc.doc_type}"
        if doc.period_label:
            sources.append(f"{label}（{doc.period_label}）: {title}")
        else:
            sources.append(f"{label}: {title}")
    return sources


def _build_documents_context(documents: List[Document]) -> List[str]:
    snippets: List[str] = []
    for doc in documents:
        title = get_document_title(doc)
        meta_parts: List[str] = []
        if doc.doc_type:
            meta_parts.append(doc.doc_type)
        if doc.period_label:
            meta_parts.append(doc.period_label)
        meta = " / ".join(meta_parts)
        preview = (doc.content_text or "").strip()
        if preview:
            preview = preview[:120]
            snippets.append(f"{title}{f'（{meta}）' if meta else ''}: {preview}")
        else:
            snippets.append(f"{title}{f'（{meta}）' if meta else ''}")
    return snippets


def _build_conversation_text(messages: List[Message]) -> str:
    lines: List[str] = []
    for msg in messages[-40:]:
        role = "ユーザー" if msg.role == "user" else "yorizo"
        stamp = msg.created_at.isoformat() if msg.created_at else ""
        lines.append(f"{stamp} {role}: {msg.content}")
    return "\n".join(lines)


def _gather_rag_context(db: Session, conversation: Conversation, query: str) -> List[str]:
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
    return snippets or []


@router.get("/{conversation_id}", response_model=ReportEnvelope)
def get_report(conversation_id: str, db: Session = Depends(get_db)) -> ReportEnvelope:
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        return ReportEnvelope(exists=False, report=None)

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    doc_filters = [Document.conversation_id == conversation_id]
    if conversation.user_id:
        doc_filters.extend([Document.user_id == conversation.user_id, Document.company_id == conversation.user_id])
    documents = (
        db.query(Document)
        .filter(or_(*doc_filters))
        .order_by(Document.uploaded_at.desc())
        .limit(20)
        .all()
    )
    profile = None
    if conversation.user_id:
        profile = (
            db.query(CompanyProfile)
            .filter(CompanyProfile.user_id == conversation.user_id)
            .first()
        )

    meta = ReportMeta(
        main_concern=conversation.main_concern,
        period=_format_period(messages, conversation),
        sources=_build_sources(profile, documents, messages),
    )

    conversation_text = _build_conversation_text(messages)
    docs_context = _build_documents_context(documents)
    _ = _gather_rag_context(
        db,
        conversation,
        conversation.main_concern or conversation.title or "経営に関する相談",
    )  # kept for future use

    finance_data = report_service.build_finance_section(
        profile=profile,
        documents=documents,
        conversation_count=db.query(Conversation).filter(Conversation.user_id == conversation.user_id).count()
        if conversation.user_id
        else len(messages),
        pending_homework_count=0,
    )
    finance_section = None
    if finance_data:
        scores = [
            ScoreItem(
                key=s["key"],
                label=s["label"],
                raw=s.get("raw"),
                industry_avg=s.get("industry_avg"),
                reason=s.get("reason") or "",
                not_enough_data=bool(s.get("not_enough_data")),
            )
            for s in finance_data.get("scores", [])
        ]
        finance_section = FinanceSection(
            overview_comment=finance_data.get("overview_comment", ""),
            scores=scores,
        )

    concerns: List[str] = []
    hints: List[str] = []

    try:
        concerns = report_service.generate_concerns(
            conversation_text=conversation_text,
            main_concern=conversation.main_concern,
            documents_summary=docs_context,
        )
    except AzureNotConfiguredError:
        concerns = report_service.fallback_concerns(messages)
    except Exception:
        logger.exception("Failed to generate concerns; using fallback")
        concerns = report_service.fallback_concerns(messages)

    try:
        hints = report_service.generate_hints(
            main_concern=conversation.main_concern,
            concerns=concerns,
            finance_section=finance_section,
            documents_summary=docs_context,
            profile=profile,
        )
    except AzureNotConfiguredError:
        hints = report_service.fallback_hints()
    except Exception:
        logger.exception("Failed to generate hints; using fallback")
        hints = report_service.fallback_hints()

    report = ReportContext(
        meta=meta,
        finance=finance_section,
        concerns=concerns,
        hints=hints,
    )
    return ReportEnvelope(exists=True, report=report)

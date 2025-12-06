from __future__ import annotations

import logging
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.enums import HomeworkStatus
from database import get_db
from app.services import reports as report_service

router = APIRouter(prefix="/report", tags=["report"])
logger = logging.getLogger(__name__)


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


class SelfActionItem(BaseModel):
    id: int
    title: str
    detail: Optional[str] = None
    status: HomeworkStatus
    due_date: Optional[date] = None
    updated_at: Optional[datetime] = None


class ReportMeta(BaseModel):
    main_concern: Optional[str] = None
    period: str
    sources: List[str]


class ReportContext(BaseModel):
    meta: ReportMeta
    finance: Optional[FinanceSection] = None
    concerns: List[str]
    hints: List[str]
    self_actions: List[SelfActionItem]


class ReportEnvelope(BaseModel):
    exists: bool
    report: Optional[ReportContext] = None


@router.get("/{conversation_id}", response_model=ReportEnvelope)
def get_report(conversation_id: str, db: Session = Depends(get_db)) -> ReportEnvelope:
    data = report_service.build_conversation_report_data(db, conversation_id)
    if not data:
        return ReportEnvelope(exists=False, report=None)

    finance_section = None
    finance_data = data.get("finance_data")
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

    self_actions = [
        SelfActionItem(
            id=task.id,
            title=task.title,
            detail=task.detail,
            status=task.status or HomeworkStatus.PENDING.value,
            due_date=task.due_date,
            updated_at=task.updated_at,
        )
        for task in data["homework_tasks"]
    ]

    report = ReportContext(
        meta=ReportMeta(**data["meta"]),
        finance=finance_section,
        concerns=data["concerns"],
        hints=data["hints"],
        self_actions=self_actions,
    )
    return ReportEnvelope(exists=True, report=report)

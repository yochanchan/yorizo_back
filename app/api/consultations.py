from datetime import date
import json
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.schemas.consultation import (
    ConsultationBookingListItem,
    ConsultationBookingListResponse,
    ConsultationMemoListItem,
    ConsultationMemoListResponse,
)
from database import get_db
from app.models import ConsultationBooking, ConsultationMemo, Conversation, Expert

router = APIRouter()


def _first_from_json(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return str(data[0])
    except Exception:
        return ""
    return ""


@router.get("/consultations", response_model=ConsultationBookingListResponse)
async def list_consultations(
    user_id: str = Query("demo-user"),
    limit: int = Query(2, ge=1),
    date_from: date | None = Query(None),
    db: Session = Depends(get_db),
) -> ConsultationBookingListResponse:
    """List upcoming consultation bookings for a user."""
    date_filter = date_from or date.today()
    query = (
        db.query(ConsultationBooking, Expert.name.label("expert_name"))
        .join(Expert, ConsultationBooking.expert_id == Expert.id)
        .filter(ConsultationBooking.date >= date_filter)
    )
    if user_id:
        query = query.filter(ConsultationBooking.user_id == user_id)

    rows: List[tuple[ConsultationBooking, str | None]] = (
        query.order_by(ConsultationBooking.date.asc(), ConsultationBooking.time_slot.asc()).limit(limit).all()
    )
    bookings = [
        ConsultationBookingListItem(
            id=booking.id,
            date=booking.date,
            time_slot=booking.time_slot,
            channel=booking.channel,
            status=booking.status,
            expert_name=expert_name,
        )
        for booking, expert_name in rows
    ]
    return ConsultationBookingListResponse(bookings=bookings)


@router.get("/consultation-memos", response_model=ConsultationMemoListResponse)
async def list_consultation_memos(
    user_id: str = Query("demo-user"),
    limit: int = Query(5, ge=1),
    db: Session = Depends(get_db),
) -> ConsultationMemoListResponse:
    """List existing consultation memos without triggering generation."""
    rows = (
        db.query(ConsultationMemo, Conversation)
        .join(Conversation, ConsultationMemo.conversation_id == Conversation.id)
        .filter(Conversation.user_id == user_id)
        .order_by(ConsultationMemo.created_at.desc())
        .limit(limit)
        .all()
    )

    memos = [
        ConsultationMemoListItem(
            conversation_id=memo.conversation_id,
            created_at=memo.created_at,
            current_point_preview=_first_from_json(memo.current_points),
            important_point_preview=_first_from_json(memo.important_points),
        )
        for memo, _conversation in rows
    ]
    return ConsultationMemoListResponse(memos=memos)

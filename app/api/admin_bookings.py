from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.schemas.booking_admin import BookingDetail, BookingListItem, BookingListResponse, BookingUpdateRequest
from app.models import BookingStatus
from database import get_db
from app.models import ConsultationBooking, Conversation

router = APIRouter()

VALID_STATUSES = {status.value for status in BookingStatus}


def _to_item(booking: ConsultationBooking) -> BookingListItem:
    expert = booking.expert
    user = booking.user
    return BookingListItem(
        id=booking.id,
        expert_id=booking.expert_id,
        expert_name=expert.name if expert else None,
        user_id=booking.user_id,
        user_name=user.nickname if user and user.nickname else None,
        conversation_id=booking.conversation_id,
        channel=booking.channel,
        status=booking.status,
        date=booking.date,
        time_slot=booking.time_slot,
        name=booking.name,
        phone=booking.phone,
        email=booking.email,
        note=booking.note,
        meeting_url=booking.meeting_url,
        line_contact=booking.line_contact,
        created_at=booking.created_at,
    )


def _to_detail(booking: ConsultationBooking) -> BookingDetail:
    item = _to_item(booking)
    return BookingDetail(**item.model_dump())


@router.get("/admin/bookings", response_model=BookingListResponse)
def list_bookings(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    channel: Optional[str] = Query(None, description="Filter by channel (online/in-person)"),
    status: Optional[BookingStatus] = Query(None, description="Filter by status"),
    expert_id: Optional[str] = Query(None, description="Filter by expert_id"),
    date_from: Optional[date] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="End date (YYYY-MM-DD, inclusive)"),
    db: Session = Depends(get_db),
) -> BookingListResponse:
    query = (
        db.query(ConsultationBooking)
        .options(joinedload(ConsultationBooking.expert), joinedload(ConsultationBooking.user))
        .order_by(ConsultationBooking.date.desc(), ConsultationBooking.created_at.desc())
    )
    if channel:
        query = query.filter(ConsultationBooking.channel == channel)
    if status:
        query = query.filter(ConsultationBooking.status == status.value)
    if expert_id:
        query = query.filter(ConsultationBooking.expert_id == expert_id)
    if date_from:
        query = query.filter(ConsultationBooking.date >= date_from)
    if date_to:
        query = query.filter(ConsultationBooking.date <= date_to)

    bookings = query.offset(offset).limit(limit).all()
    items = [_to_item(booking) for booking in bookings]
    return BookingListResponse(bookings=items)


@router.get("/admin/bookings/{booking_id}", response_model=BookingDetail)
def get_booking_detail(booking_id: str, db: Session = Depends(get_db)) -> BookingDetail:
    booking = (
        db.query(ConsultationBooking)
        .options(joinedload(ConsultationBooking.expert), joinedload(ConsultationBooking.user))
        .filter(ConsultationBooking.id == booking_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return _to_detail(booking)


@router.patch("/admin/bookings/{booking_id}", response_model=BookingDetail)
def update_booking(booking_id: str, payload: BookingUpdateRequest, db: Session = Depends(get_db)) -> BookingDetail:
    booking = db.query(ConsultationBooking).filter(ConsultationBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if payload.status:
        if payload.status.value not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        booking.status = payload.status.value
    if payload.note is not None:
        booking.note = payload.note
    if payload.conversation_id is not None:
        if payload.conversation_id == "":
            booking.conversation_id = None
        else:
            conversation = db.query(Conversation).filter(Conversation.id == payload.conversation_id).first()
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")
            booking.conversation_id = conversation.id
            if conversation.user_id and not booking.user_id:
                booking.user_id = conversation.user_id
    if payload.meeting_url is not None:
        booking.meeting_url = payload.meeting_url
    if payload.line_contact is not None:
        booking.line_contact = payload.line_contact

    db.commit()
    db.refresh(booking)

    booking = (
        db.query(ConsultationBooking)
        .options(joinedload(ConsultationBooking.expert), joinedload(ConsultationBooking.user))
        .filter(ConsultationBooking.id == booking_id)
        .first()
    )
    return _to_detail(booking)

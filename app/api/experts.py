from collections import defaultdict
from datetime import datetime, timedelta
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.schemas.expert import (
    ConsultationBookingRequest,
    ConsultationBookingResponse,
    ExpertAvailabilityResponse,
    ExpertResponse,
)
from database import get_db
from app.models import ConsultationBooking, Conversation, Expert, ExpertAvailability, User
from app.models.enums import BookingStatus
from app.services import booking_rules

router = APIRouter()


def _seed_experts_if_needed(db: Session) -> None:
    if db.query(Expert).count() > 0:
        return

    expert1 = Expert(
        name="田中 経営太郎",
        title="売上拡大・資金繰り支援",
        organization="福岡県よろず支援拠点",
        tags=json.dumps(["売上拡大", "飲食店支援", "資金繰り"], ensure_ascii=False),
        rating=4.8,
        review_count=124,
        location_prefecture="福岡県",
        description="飲食店経営の経験とマーケティング支援の実績で、数字を見ながら現場が回る仕組みを提案します。",
        avatar_url=None,
    )
    expert2 = Expert(
        name="佐藤 真奈美",
        title="人材・IT/DX 専門",
        organization="福岡県よろず支援拠点",
        tags=json.dumps(["人材採用", "IT/DX", "補助金"], ensure_ascii=False),
        rating=4.7,
        review_count=98,
        location_prefecture="福岡県",
        description="バックオフィス改善と補助金活用に強み。採用・定着とクラウド導入をセットで提案します。",
        avatar_url=None,
    )
    db.add_all([expert1, expert2])
    db.commit()

    start, end = booking_rules.booking_window()
    sample_dates: List = []
    current = start
    while current <= end and len(sample_dates) < 10:
        if not booking_rules.is_closed_day(current):
            sample_dates.append(current)
        current += timedelta(days=1)
    if not sample_dates:
        sample_dates.append(start)
    slots = json.dumps(["10:00-11:00", "11:00-12:00", "14:00-15:00", "16:00-17:00"], ensure_ascii=False)
    for exp in [expert1, expert2]:
        for d in sample_dates:
            db.add(ExpertAvailability(expert_id=exp.id, date=d, slots_json=slots))
    db.commit()


def _tags_to_list(raw: str | None) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(t) for t in data]
    except Exception:
        pass
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


@router.get("/experts", response_model=List[ExpertResponse])
async def list_experts(db: Session = Depends(get_db)) -> List[ExpertResponse]:
    _seed_experts_if_needed(db)
    experts = db.query(Expert).all()
    return [
        ExpertResponse(
            id=exp.id,
            name=exp.name,
            avatar_url=exp.avatar_url,
            title=exp.title,
            organization=exp.organization,
            tags=_tags_to_list(exp.tags),
            rating=exp.rating,
            review_count=exp.review_count,
            location_prefecture=exp.location_prefecture,
            description=exp.description,
        )
        for exp in experts
    ]


@router.get("/experts/{expert_id}/availability", response_model=ExpertAvailabilityResponse)
async def get_expert_availability(expert_id: str, db: Session = Depends(get_db)) -> ExpertAvailabilityResponse:
    _seed_experts_if_needed(db)
    expert = db.query(Expert).filter(Expert.id == expert_id).first()
    if not expert:
        raise HTTPException(status_code=404, detail="Expert not found")

    availabilities = (
        db.query(ExpertAvailability)
        .filter(ExpertAvailability.expert_id == expert_id)
        .order_by(ExpertAvailability.date.asc())
        .all()
    )

    if not availabilities:
        start, end = booking_rules.booking_window()
        sample_dates: List = []
        current = start
        while current <= end and len(sample_dates) < 10:
            if not booking_rules.is_closed_day(current):
                sample_dates.append(current)
            current += timedelta(days=1)
        slots = json.dumps(["10:00-11:00", "11:00-12:00", "14:00-15:00", "16:00-17:00"], ensure_ascii=False)
        for d in sample_dates:
            db.add(ExpertAvailability(expert_id=expert_id, date=d, slots_json=slots))
        db.commit()
        availabilities = (
            db.query(ExpertAvailability)
            .filter(ExpertAvailability.expert_id == expert_id)
            .order_by(ExpertAvailability.date.asc())
            .all()
        )

    start_date, end_date = booking_rules.booking_window()
    bookings = (
        db.query(ConsultationBooking)
        .filter(
            ConsultationBooking.expert_id == expert_id,
            ConsultationBooking.date >= start_date,
            ConsultationBooking.date <= end_date,
            ConsultationBooking.status != BookingStatus.CANCELLED.value,
        )
        .all()
    )
    booked_by_date: dict = defaultdict(set)
    for booking in bookings:
        booked_by_date[booking.date].add(booking.time_slot)

    availability_items = []
    for item in availabilities:
        if item.date < start_date or item.date > end_date:
            continue
        if booking_rules.is_closed_day(item.date):
            continue
        slots = json.loads(item.slots_json) if item.slots_json else []
        if not slots:
            continue
        booked_slots = [slot for slot in slots if slot in booked_by_date.get(item.date, set())]
        available_count = len([slot for slot in slots if slot not in booked_by_date.get(item.date, set())])
        availability_items.append(
            {
                "date": item.date,
                "slots": slots,
                "booked_slots": booked_slots,
                "available_count": available_count,
            }
        )

    return ExpertAvailabilityResponse(expert_id=expert_id, availability=availability_items)


@router.post("/consultations", response_model=ConsultationBookingResponse)
async def create_consultation_booking(
    payload: ConsultationBookingRequest, db: Session = Depends(get_db)
) -> ConsultationBookingResponse:
    expert = db.query(Expert).filter(Expert.id == payload.expert_id).first()
    if not expert:
        raise HTTPException(status_code=404, detail="Expert not found")

    today = booking_rules.get_jst_today()
    if not booking_rules.is_within_booking_window(payload.date, today) or booking_rules.is_closed_day(payload.date):
        raise HTTPException(status_code=400, detail="Booking date is not available")

    availability = (
        db.query(ExpertAvailability)
        .filter(ExpertAvailability.expert_id == payload.expert_id, ExpertAvailability.date == payload.date)
        .first()
    )
    valid_slots = json.loads(availability.slots_json) if availability and availability.slots_json else []
    if not valid_slots:
        raise HTTPException(status_code=400, detail="Selected date is not available")
    if payload.time_slot not in valid_slots:
        raise HTTPException(status_code=400, detail="Selected time slot is not available")

    conversation = None
    if payload.conversation_id:
        conversation = db.query(Conversation).filter(Conversation.id == payload.conversation_id).first()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation and payload.user_id and conversation.user_id and conversation.user_id != payload.user_id:
        raise HTTPException(status_code=400, detail="Conversation user does not match booking user")

    user_id_value = payload.user_id or (conversation.user_id if conversation else None)

    user: User | None = None
    if user_id_value:
        user = db.query(User).filter(User.id == user_id_value).first()
        if not user:
            user = User(id=user_id_value, nickname=None)
            db.add(user)
            db.commit()

    existing = (
        db.query(ConsultationBooking)
        .filter(
            ConsultationBooking.expert_id == payload.expert_id,
            ConsultationBooking.date == payload.date,
            ConsultationBooking.time_slot == payload.time_slot,
            ConsultationBooking.status != BookingStatus.CANCELLED.value,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Selected time slot is already booked")

    booking = ConsultationBooking(
        expert_id=payload.expert_id,
        user_id=user.id if user else None,
        conversation_id=conversation.id if conversation else payload.conversation_id,
        date=payload.date,
        time_slot=payload.time_slot,
        channel=payload.channel,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        note=payload.note,
        meeting_url=payload.meeting_url,
        line_contact=payload.line_contact,
        created_at=datetime.utcnow(),
    )
    db.add(booking)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Selected time slot is already booked")
    db.refresh(booking)

    return ConsultationBookingResponse(
        booking_id=booking.id,
        expert_id=booking.expert_id,
        conversation_id=booking.conversation_id,
        date=booking.date,
        time_slot=booking.time_slot,
        channel=booking.channel,
        meeting_url=booking.meeting_url,
        line_contact=booking.line_contact,
        message="予約を受け付けました。よろず支援拠点からの連絡をお待ちください。",
    )

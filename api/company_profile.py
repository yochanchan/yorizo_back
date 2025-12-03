from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.schemas.company_profile import CompanyProfilePayload, CompanyProfileResponse
from database import get_db
from models import CompanyProfile, User

router = APIRouter()


def _ensure_user(db: Session, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, nickname="guest")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.get("/company-profile/{user_id}", response_model=CompanyProfileResponse)
async def get_company_profile(user_id: str, db: Session = Depends(get_db)) -> CompanyProfileResponse:
    profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).first()
    if not profile:
        now = datetime.utcnow()
        profile = CompanyProfile(
            user_id=user_id,
            company_name=None,
            industry=None,
            employees_range=None,
            annual_sales_range=None,
            location_prefecture=None,
            years_in_business=None,
            created_at=now,
            updated_at=now,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return CompanyProfileResponse.model_validate(profile, from_attributes=True)


@router.post("/company-profile/{user_id}", response_model=CompanyProfileResponse)
async def upsert_company_profile(
    user_id: str, payload: CompanyProfilePayload, db: Session = Depends(get_db)
) -> CompanyProfileResponse:
    _ensure_user(db, user_id)
    profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).first()
    now = datetime.utcnow()
    if not profile:
        profile = CompanyProfile(
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(profile)

    for field, value in payload.model_dump().items():
        setattr(profile, field, value)
    profile.updated_at = now
    db.commit()
    db.refresh(profile)
    return CompanyProfileResponse.model_validate(profile, from_attributes=True)

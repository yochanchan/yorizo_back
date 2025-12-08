from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CompanyProfilePayload(BaseModel):
    company_name: Optional[str] = None
    industry: Optional[str] = None
    employees_range: Optional[str] = None
    annual_sales_range: Optional[str] = None
    location_prefecture: Optional[str] = None
    years_in_business: Optional[int] = None
    business_type: Optional[str] = None
    founded_year: Optional[int] = None
    city: Optional[str] = None
    main_bank: Optional[str] = None
    has_loan: Optional[str] = None
    has_rent: Optional[str] = None
    owner_age: Optional[str] = None
    main_concern: Optional[str] = None


class CompanyProfileResponse(CompanyProfilePayload):
    user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

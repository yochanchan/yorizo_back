from pydantic import BaseModel
from typing import List, Dict, Optional


class KPIValue(BaseModel):
    key: str
    label: str
    raw: Optional[float]
    value_display: str
    unit: Optional[str] = None
    score: Optional[int]


class RadarPeriod(BaseModel):
    label: str
    scores: List[Optional[float]]
    raw_values: List[Optional[float]]
    kpis: List[KPIValue] = []


class RadarSection(BaseModel):
    axes: List[str]
    periods: List[RadarPeriod]


class QualitativeBlock(BaseModel):
    keieisha: Dict[str, str]
    jigyo: Dict[str, str]
    kankyo: Dict[str, str]
    naibu: Dict[str, str]


class CompanySummary(BaseModel):
    id: str | int
    company_name: str | None = None
    name: str | None = None
    industry: str | None = None
    employees: int | None = None
    employees_range: str | None = None
    annual_sales_range: str | None = None
    annual_revenue_range: str | None = None


class CompanyReportResponse(BaseModel):
    company: CompanySummary
    radar: RadarSection
    qualitative: QualitativeBlock
    current_state: str
    future_goal: str
    action_plan: str
    snapshot_strengths: List[str] = []
    snapshot_weaknesses: List[str] = []
    desired_image: str | None = None
    gap_summary: str | None = None
    thinking_questions: List[str] = []

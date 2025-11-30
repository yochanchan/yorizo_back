from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class LocalBenchmarkScore(BaseModel):
    label: str
    description: str
    score: Optional[int] = None


class LocalBenchmarkAxis(BaseModel):
    id: str
    label: str
    score: int


class CompanyAnalysisCategory(BaseModel):
    category: str
    items: List[str]


class LocalBenchmark(BaseModel):
    axes: List[LocalBenchmarkAxis]


class CompanyAnalysisReport(BaseModel):
    company_id: str
    last_updated_at: Optional[datetime] = None
    summary: str
    basic_info_note: str
    finance_scores: List[LocalBenchmarkScore]
    pain_points: List[CompanyAnalysisCategory]
    strengths: List[str]
    weaknesses: List[str]
    action_items: List[str]
    local_benchmark: Optional[LocalBenchmark] = None

    model_config = ConfigDict(from_attributes=True)

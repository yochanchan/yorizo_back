from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.schemas.reports import CompanyAnalysisReport
from database import get_db
from app.services.reports import build_company_analysis_report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/company-analysis", response_model=CompanyAnalysisReport)
def get_company_analysis_report(
    company_id: str = Query(..., min_length=1, description="ID of the company/user"),
    db: Session = Depends(get_db),
) -> CompanyAnalysisReport:
    try:
        return build_company_analysis_report(db, company_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

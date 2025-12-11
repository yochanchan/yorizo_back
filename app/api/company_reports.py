from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.schemas.company_report import CompanyReportResponse, QualitativeBlock, RadarSection
from app.services.company_report import AXES, build_company_report
from database import get_db

router = APIRouter(prefix="/companies", tags=["companies"])
logger = logging.getLogger(__name__)


def _empty_report() -> CompanyReportResponse:
    placeholder = "レポートを生成できませんでした。最低限の情報のみを返します。"
    radar = RadarSection(axes=AXES, periods=[])
    qual = QualitativeBlock(keieisha={}, jigyo={}, kankyo={}, naibu={})
    company_stub = {
        "id": "unknown",
        "company_name": None,
        "name": None,
        "industry": None,
        "employees": None,
        "employees_range": None,
        "annual_sales_range": None,
        "annual_revenue_range": None,
    }
    return CompanyReportResponse(
        company=company_stub,  # type: ignore[arg-type]
        radar=radar,
        qualitative=qual,
        current_state=placeholder,
        future_goal=placeholder,
        action_plan=placeholder,
    )


@router.get("/{company_id}/report", response_model=CompanyReportResponse)
def get_company_report_endpoint(company_id: str, db: Session = Depends(get_db)) -> CompanyReportResponse:
    try:
        return build_company_report(db, company_id)
    except ValueError as exc:
        logger.warning("Company not found; returning empty report: %s", exc)
        return _empty_report()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to build company report")
        raise HTTPException(status_code=500, detail="Failed to build company report") from exc

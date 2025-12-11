from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict


class FinancialStatementBase(BaseModel):
    company_id: str
    document_id: Optional[str] = None
    fiscal_year: Optional[int] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    sales: Optional[float] = None
    operating_profit: Optional[float] = None
    ordinary_profit: Optional[float] = None
    net_income: Optional[float] = None
    total_assets: Optional[float] = None
    net_assets: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class FinancialStatementRead(FinancialStatementBase):
    id: int


__all__ = ["FinancialStatementBase", "FinancialStatementRead"]

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, Numeric
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE


class FinancialStatement(Base):
    __tablename__ = "financial_statements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(GUID_TYPE, ForeignKey("companies.id"), index=True, nullable=False)
    fiscal_year = Column(Integer, nullable=False)

    sales = Column(Numeric(18, 2))
    operating_profit = Column(Numeric(18, 2))
    ordinary_profit = Column(Numeric(18, 2))
    net_income = Column(Numeric(18, 2))
    depreciation = Column(Numeric(18, 2))
    labor_cost = Column(Numeric(18, 2))

    current_assets = Column(Numeric(18, 2))
    current_liabilities = Column(Numeric(18, 2))
    fixed_assets = Column(Numeric(18, 2))
    total_assets = Column(Numeric(18, 2))
    equity = Column(Numeric(18, 2))
    total_liabilities = Column(Numeric(18, 2))
    employees = Column(Integer)
    cash_and_deposits = Column(Numeric(18, 2))
    receivables = Column(Numeric(18, 2))
    inventory = Column(Numeric(18, 2))
    payables = Column(Numeric(18, 2))
    borrowings = Column(Numeric(18, 2))
    interest_bearing_debt = Column(Numeric(18, 2))
    previous_sales = Column(Numeric(18, 2))

    company = relationship("Company", back_populates="financial_statements")


__all__ = ["FinancialStatement"]

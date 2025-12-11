from __future__ import annotations

from sqlalchemy import Column, Date, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE


class FinancialStatement(Base):
    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_financial_statements_document_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(GUID_TYPE, ForeignKey("companies.id"), index=True, nullable=False)
    document_id = Column(GUID_TYPE, ForeignKey("documents.id"), nullable=True)
    fiscal_year = Column(Integer, nullable=True)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)

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
    document = relationship("Document", backref="financial_statement", uselist=False)

    @property
    def net_assets(self):
        # Alias for equity to align with API naming.
        return getattr(self, "equity", None)

    @net_assets.setter
    def net_assets(self, value):
        self.equity = value


__all__ = ["FinancialStatement"]

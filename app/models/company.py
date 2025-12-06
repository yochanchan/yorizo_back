from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base
from app.models.base import GUID_TYPE, default_uuid, utcnow


class Company(Base):
    __tablename__ = "companies"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=True, index=True)
    # Newer fields for canonical company summary
    name = Column(String(255), nullable=True)
    employees = Column(Integer, nullable=True)
    annual_revenue_range = Column(String(100), nullable=True)
    # Legacy fields kept for backward compatibility with existing data
    company_name = Column(String(255), nullable=True)
    industry = Column(String(255), nullable=True)
    employees_range = Column(String(50), nullable=True)
    annual_sales_range = Column(String(50), nullable=True)
    location_prefecture = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    owner = relationship("User", back_populates="companies")
    financial_statements = relationship(
        "FinancialStatement", back_populates="company", cascade="all, delete-orphan"
    )


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    id = Column(GUID_TYPE, primary_key=True, default=default_uuid)
    user_id = Column(GUID_TYPE, ForeignKey("users.id"), nullable=False, unique=True)
    company_name = Column(String(255), nullable=True)
    name = Column(String(255), nullable=True)
    industry = Column(String(255), nullable=True)
    employees = Column(Integer, nullable=True)
    employees_range = Column(String(50), nullable=True)
    annual_sales_range = Column(String(50), nullable=True)
    annual_revenue_range = Column(String(100), nullable=True)
    location_prefecture = Column(String(100), nullable=True)
    years_in_business = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="company_profile")


__all__ = ["Company", "CompanyProfile"]

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import FinancialStatement
from app.services.financial_statement_parser import parse_financial_statement_pdf, parse_japanese_sme_statement

logger = logging.getLogger(__name__)

FINANCIAL_FIELDS = [
    "sales",
    "operating_profit",
    "ordinary_profit",
    "net_income",
    "depreciation",
    "labor_cost",
    "current_assets",
    "current_liabilities",
    "fixed_assets",
    "total_assets",
    "equity",
    "total_liabilities",
    "employees",
    "cash_and_deposits",
    "receivables",
    "inventory",
    "payables",
    "borrowings",
    "interest_bearing_debt",
    "previous_sales",
]


def upsert_financial_rows(db: Session, company_id: str, rows: List[Dict[str, Optional[float]]]) -> None:
    """
    Insert or update financial statements for a company.
    - Uses company_id + fiscal_year as the natural key.
    - If previous_sales is missing, infer from the prior row's sales (rows should be sorted by year desc).
    """
    if not rows:
        return

    normalized = [row for row in rows if row.get("fiscal_year") is not None]
    if not normalized:
        return

    # sort latest -> past
    normalized = sorted(normalized, key=lambda r: r["fiscal_year"], reverse=True)

    # backfill previous_sales if possible
    for idx, row in enumerate(normalized):
        if row.get("previous_sales") is None and idx + 1 < len(normalized):
            row["previous_sales"] = normalized[idx + 1].get("sales")

    for row in normalized:
        fiscal_year = row["fiscal_year"]
        stmt = (
            db.query(FinancialStatement)
            .filter(
                FinancialStatement.company_id == company_id,
                FinancialStatement.fiscal_year == fiscal_year,
            )
            .first()
        )
        if not stmt:
            stmt = FinancialStatement(company_id=company_id, fiscal_year=fiscal_year)
            db.add(stmt)
        for field in FINANCIAL_FIELDS:
            if field in row and row[field] is not None:
                setattr(stmt, field, row[field])
    db.commit()


def upsert_from_pdf(db: Session, company_id: str, file_path: str) -> Optional[FinancialStatement]:
    data = parse_japanese_sme_statement(file_path)
    fiscal_year = data.get("fiscal_year") if data else None
    if fiscal_year is None:
        return None

    stmt = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.company_id == company_id,
            FinancialStatement.fiscal_year == fiscal_year,
        )
        .first()
    )
    if not stmt:
        stmt = FinancialStatement(company_id=company_id, fiscal_year=fiscal_year)
        db.add(stmt)

    for field, value in data.items():
        if field == "fiscal_year":
            continue
        if hasattr(stmt, field) and value is not None:
            setattr(stmt, field, value)

    db.commit()
    db.refresh(stmt)
    return stmt


def upsert_financial_statements_from_pdf(
    db: Session,
    company_id: str,
    fiscal_year: int,
    file_path: str,
) -> Optional[FinancialStatement]:
    metrics = parse_financial_statement_pdf(file_path, fiscal_year_hint=fiscal_year) or {}
    numeric_count = len({k: v for k, v in metrics.items() if k != "fiscal_year" and v is not None})
    if not metrics or numeric_count < 1:
        logger.warning(
            "Skipped financial statement upsert: insufficient metrics parsed for company=%s year=%s",
            company_id,
            fiscal_year,
        )
        return None

    stmt = (
        db.query(FinancialStatement)
        .filter(
            FinancialStatement.company_id == company_id,
            FinancialStatement.fiscal_year == fiscal_year,
        )
        .first()
    )
    if not stmt:
        stmt = FinancialStatement(company_id=company_id, fiscal_year=fiscal_year)
        db.add(stmt)

    for field, value in metrics.items():
        if field == "fiscal_year":
            continue
        if hasattr(stmt, field) and value is not None:
            setattr(stmt, field, value)
    db.commit()
    db.refresh(stmt)
    logger.info(
        "Parsed financial statement for company %s fiscal_year %s: %s",
        company_id,
        fiscal_year,
        {k: v for k, v in metrics.items() if k != "fiscal_year"},
    )
    return stmt


def upsert_financial_statement_from_pdf(
    db: Session,
    company_id: str,
    fiscal_year: int,
    file_path: str,
) -> Optional[FinancialStatement]:
    """
    Backward-compatible wrapper: upsert parsed financial statement row for a company/year.
    """
    return upsert_financial_statements_from_pdf(db, company_id, fiscal_year, file_path)


__all__ = [
    "upsert_financial_rows",
    "upsert_from_pdf",
    "upsert_financial_statements_from_pdf",
    "upsert_financial_statement_from_pdf",
    "FINANCIAL_FIELDS",
]

from __future__ import annotations

from typing import Dict, Any

from sqlalchemy.orm import Session

from app import models

UPSERT_FIELDS = [
    "fiscal_year",
    "sales",
    "operating_profit",
    "ordinary_profit",
    "net_income",
    "total_assets",
    "net_assets",
]


def upsert_financial_statement_for_document(
    db: Session,
    company_id: str,
    document_id: str,
    parsed: Dict[str, Any],
) -> models.FinancialStatement:
    stmt = (
        db.query(models.FinancialStatement)
        .filter(models.FinancialStatement.document_id == document_id)
        .first()
    )
    if stmt is None:
        stmt = models.FinancialStatement(
            company_id=company_id,
            document_id=document_id,
        )
        db.add(stmt)
    elif company_id and stmt.company_id != company_id:
        stmt.company_id = company_id

    for field in UPSERT_FIELDS:
        if field in parsed:
            target_field = "equity" if field == "net_assets" else field
            setattr(stmt, target_field, parsed[field])

    db.commit()
    db.refresh(stmt)
    return stmt


__all__ = ["upsert_financial_statement_for_document"]

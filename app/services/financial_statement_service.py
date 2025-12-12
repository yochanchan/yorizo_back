from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import FinancialStatement
from app.services.financial_statement_parser import parse_financial_statement_pdf, parse_japanese_sme_statement

try:
    import pdfplumber  # type: ignore
except ImportError:  # pragma: no cover - optional dependency for PDF parsing
    pdfplumber = None

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

# --- PDF parsing helpers ---


def _parse_number(text: str) -> Optional[Decimal]:
    """
    Extract a single numeric value from a line of text.
    Handles formats like '69,249,742', '1,180,832', '1,180.83円'.
    Picks the last numeric token to avoid concatenation.
    """
    candidates = re.findall(r"[+-]?\d[\d,]*\.?\d*", text)
    if not candidates:
        return None
    token = candidates[-1].replace(",", "")
    if token in ("", "+", "-"):
        return None
    try:
        return Decimal(token)
    except Exception:
        return None


def parse_financial_pdf(path: str) -> Dict[str, Decimal]:
    """
    Parse a Japanese BS/PL PDF and return key metrics.
    Focused on typical SME statement layouts (PL + BS totals).
    """
    if pdfplumber is None:
        logger.warning("pdfplumber is not installed; skipping PDF parse for %s", path)
        return {}
    data: Dict[str, Decimal] = {}
    label_map = {
        "売上高": "sales",
        "営業利益": "operating_profit",
        "経常利益": "ordinary_profit",
        "当期純利益": "net_income",
        "減価償却費": "depreciation",
        "流動資産合計": "current_assets",
        "流動負債合計": "current_liabilities",
        "固定資産合計": "fixed_assets",
        "資産合計": "total_assets",
        "純資産合計": "equity",
        "株主資本合計": "equity",
        "負債合計": "total_liabilities",
        "短期借入金": "borrowings",
        "長期借入金": "borrowings",
    }
    label_norm_map = {re.sub(r"\s+", "", k): v for k, v in label_map.items()}

    def _normalize(text: str) -> str:
        return re.sub(r"\s+", "", text)

    try:
        lines: List[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                txt = page.extract_text() or ""
                for line in txt.splitlines():
                    line = line.strip()
                    if line:
                        lines.append(line)
    except Exception:
        logger.exception("Failed to open PDF for financial parsing: %s", path)
        return data

    pending_key: Optional[str] = None
    for line in lines:
        text = line.strip()
        if not text:
            continue

        norm = _normalize(text)
        num = _parse_number(text)

        matched_label = False
        for jp_label, key in label_map.items():
            label_norm = _normalize(jp_label)

            if key == "net_income":
                # Skip per-share or pre-tax variants
                if "一株当たりの当期純利益" in norm or "税引前当期純利益" in norm:
                    continue

            if label_norm in norm:
                matched_label = True
                if num is not None:
                    if key == "borrowings":
                        data["borrowings"] = data.get("borrowings", Decimal(0)) + num
                    else:
                        data.setdefault(key, num)
                else:
                    pending_key = key
                break

        # If previous line was label-only and current line is numeric-only (no label)
        if pending_key and num is not None:
            if not matched_label and not any(lbl_norm in norm for lbl_norm in label_norm_map.keys()):
                if pending_key == "borrowings":
                    data["borrowings"] = data.get("borrowings", Decimal(0)) + num
                else:
                    data.setdefault(pending_key, num)
                pending_key = None

    if "borrowings" in data:
        data.setdefault("interest_bearing_debt", data["borrowings"])

    logger.info("Parsed financial PDF %s -> %s", path, data)
    return data


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
    document_id: Optional[str],
    file_path: str,
) -> Optional[FinancialStatement]:
    metrics = parse_financial_pdf(file_path)

    stmt = None
    if document_id:
        stmt = db.query(FinancialStatement).filter(FinancialStatement.document_id == document_id).first()
    if stmt is None:
        stmt = (
            db.query(FinancialStatement)
            .filter(
                FinancialStatement.company_id == company_id,
                FinancialStatement.fiscal_year == fiscal_year,
            )
            .first()
        )
    if not stmt:
        stmt = FinancialStatement(company_id=company_id, fiscal_year=fiscal_year, document_id=document_id)
        db.add(stmt)
    elif document_id:
        stmt.document_id = document_id

    for field, value in metrics.items():
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
    return upsert_financial_statements_from_pdf(db, company_id, fiscal_year, document_id=None, file_path=file_path)


__all__ = [
    "upsert_financial_rows",
    "upsert_from_pdf",
    "upsert_financial_statements_from_pdf",
    "upsert_financial_statement_from_pdf",
    "FINANCIAL_FIELDS",
]

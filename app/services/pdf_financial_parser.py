from __future__ import annotations

import datetime
import logging
import re
from typing import Dict, List, Optional

import pypdf

logger = logging.getLogger(__name__)

# Japanese label patterns mapped to FinancialStatement fields
LABEL_MAP: Dict[str, str] = {
    r"売上高|売上金額|売上高合計": "sales",
    r"営業利益": "operating_profit",
    r"経常利益": "ordinary_profit",
    r"当期純利益|当期純損益|純利益": "net_income",
    r"減価償却費": "depreciation",
    r"人件費|給料賃金": "labor_cost",
    r"流動資産": "current_assets",
    r"固定資産": "fixed_assets",
    r"負債合計|負債総額|総負債": "total_liabilities",
    r"流動負債": "current_liabilities",
    r"純資産|自己資本": "equity",
    r"現金及び預金|現金預金|現預金": "cash_and_deposits",
    r"短期借入金|長期借入金|借入金": "borrowings",
    r"従業員|従業員数": "employees",
}


def _parse_number(token: str) -> Optional[float]:
    cleaned = token.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", "")
    cleaned = cleaned.replace("△", "-").replace("▲", "-").replace("−", "-")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except Exception:
        return None


def _extract_numbers(line: str) -> List[float]:
    numbers: List[float] = []
    for token in re.findall(r"[△▲−-]?[\d,]+(?:\.\d+)?", line):
        num = _parse_number(token)
        if num is not None:
            numbers.append(num)
    return numbers


def _extract_years(lines: List[str]) -> List[int]:
    years: List[int] = []
    for line in lines:
        for match in re.findall(r"(20\d{2})", line):
            year = int(match)
            if year not in years:
                years.append(year)
    return sorted(years, reverse=True)[:3]


def parse_financial_pdf(file_path: str) -> List[Dict[str, Optional[float]]]:
    """
    Lightweight PDF parser for Japanese financial statements.
    - Uses pypdf to extract text.
    - Matches known labels and maps first three numeric columns to latest->past.
    - Returns a list of dicts with fiscal_year and financial fields.
    """
    try:
        reader = pypdf.PdfReader(file_path)
        lines: List[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            lines.extend([line.strip() for line in page_text.splitlines() if line.strip()])
    except Exception as exc:
        logger.exception("Failed to parse PDF for financials", exc_info=exc)
        return []

    if not lines:
        return []

    years = _extract_years(lines)
    if not years:
        current_year = datetime.datetime.utcnow().year
        years = [current_year - i for i in range(3)]

    rows: List[Dict[str, Optional[float]]] = [dict(fiscal_year=years[idx]) for idx in range(len(years))]

    for line in lines:
        for pattern, field in LABEL_MAP.items():
            if re.search(pattern, line):
                nums = _extract_numbers(line)
                if not nums:
                    continue
                for idx, num in enumerate(nums[: len(rows)]):
                    rows[idx][field] = num
                break

    final_rows: List[Dict[str, Optional[float]]] = []
    for row in rows:
        data_points = {k: v for k, v in row.items() if k != "fiscal_year" and v is not None}
        if data_points:
            final_rows.append(row)

    return final_rows


__all__ = ["parse_financial_pdf"]

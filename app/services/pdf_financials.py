from __future__ import annotations

import logging
import re
from typing import List, Optional, TypedDict

import pdfplumber

logger = logging.getLogger(__name__)


class ParsedFinancials(TypedDict, total=False):
    fiscal_year: Optional[int]
    sales: Optional[float]
    operating_profit: Optional[float]
    ordinary_profit: Optional[float]
    net_income: Optional[float]
    total_assets: Optional[float]
    net_assets: Optional[float]


def _to_number(token: str) -> Optional[float]:
    cleaned = token.strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", "")
    cleaned = cleaned.replace("▲", "-").replace("△", "-").replace("−", "-").replace("－", "-")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except Exception:
        return None


def _find_number(text: str, keywords: List[str]) -> Optional[float]:
    if not keywords:
        return None
    pattern = rf"({'|'.join(map(re.escape, keywords))})[^\d\-△▲－−]*([\-△▲－−(]?\d[\d,\.]*)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return _to_number(match.group(2))


def _find_fiscal_year(text: str) -> Optional[int]:
    year_match = re.search(r"(20\d{2})\s*年", text)
    if year_match:
        try:
            return int(year_match.group(1))
        except ValueError:
            return None
    return None


def parse_financial_pdf(file_path: str) -> ParsedFinancials:
    """
    Parse a financial statement PDF (text-based) and extract key metrics using
    simple regex heuristics. Returns a partial dict; any missing values are None.
    """
    try:
        with pdfplumber.open(file_path) as pdf:
            texts = [page.extract_text() or "" for page in pdf.pages]
    except Exception:
        logger.exception("Failed to open PDF for financial parsing: %s", file_path)
        return {}

    full_text = "\n".join(texts)
    if not full_text.strip():
        return {}

    normalized = re.sub(r"\s+", " ", full_text)

    result: ParsedFinancials = {}
    fy = _find_fiscal_year(normalized)
    if fy:
        result["fiscal_year"] = fy

    field_keywords = {
        "sales": ["売上高", "売上金額", "営業収益"],
        "operating_profit": ["営業利益"],
        "ordinary_profit": ["経常利益"],
        "net_income": ["当期純利益", "当期損益"],
        "total_assets": ["総資産", "資産合計"],
        "net_assets": ["純資産", "自己資本"],
    }

    for field, keywords in field_keywords.items():
        value = _find_number(normalized, keywords)
        if value is not None:
            result[field] = value

    return result


__all__ = ["ParsedFinancials", "parse_financial_pdf"]

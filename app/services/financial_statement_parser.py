from __future__ import annotations

import datetime
import logging
import re
from typing import Dict, List, Optional, Tuple

import pdfplumber

logger = logging.getLogger(__name__)


LABEL_MAP: Dict[str, str] = {
    "売上高": "sales",
    "売上金額": "sales",
    "営業利益": "operating_profit",
    "営業損益": "operating_profit",
    "経常利益": "ordinary_profit",
    "経常損益": "ordinary_profit",
    "当期純利益": "net_income",
    "当期利益": "net_income",
    "減価償却費": "depreciation",
    "人件費": "labor_cost",
    "流動資産合計": "current_assets",
    "流動資産": "current_assets",
    "流動負債合計": "current_liabilities",
    "流動負債": "current_liabilities",
    "固定資産合計": "fixed_assets",
    "固定資産": "fixed_assets",
    "総資産": "total_assets",
    "資産合計": "total_assets",
    "純資産": "equity",
    "自己資本": "equity",
    "負債合計": "total_liabilities",
    "従業員数": "employees",
}


def _to_half_width(text: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKC", text)


def _detect_unit_multiplier(text: str) -> int:
    if "百万円" in text or "百万円単位" in text:
        return 1_000_000
    if "千円" in text:
        return 1_000
    return 1


def _normalize_text(text: str) -> List[str]:
    normalized_lines: List[str] = []
    for raw in text.splitlines():
        line = _to_half_width(raw)
        line = line.replace(",", "").replace("，", "").strip()
        line = re.sub(r"[ \t]+", " ", line)
        if line:
            normalized_lines.append(line)
    return normalized_lines


def _extract_year_from_line(line: str) -> Optional[int]:
    # Western year
    m = re.search(r"(20\d{2})年", line)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # Heisei/Reiwa
    era = re.search(r"(平成|令和)\s*(\d{1,2})年", line)
    if era:
        try:
            era_year = int(era.group(2))
            if era.group(1) == "平成":
                return 1988 + era_year
            if era.group(1) == "令和":
                return 2018 + era_year
        except ValueError:
            pass
    return None


def _detect_fiscal_year(lines: List[str]) -> Optional[int]:
    for line in lines:
        year = _extract_year_from_line(line)
        if year:
            return year
    return None


def _find_last_int_on_line(line: str) -> Optional[int]:
    matches = re.findall(r"-?\d+", line)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def _parse_metrics(lines: List[str], multiplier: int) -> Dict[str, int]:
    metrics: Dict[str, int] = {}
    for line in lines:
        for label, field in LABEL_MAP.items():
            if label in line:
                val = _find_last_int_on_line(line)
                if val is None:
                    continue
                metrics[field] = val * multiplier
    return metrics


def parse_financial_statement_pdf(file_path: str, fiscal_year_hint: Optional[int] = None) -> Dict[str, Optional[int]]:
    """
    Deterministic parser for Japanese SME financial statements using pdfplumber.
    Returns a dict of parsed metrics; missing values are omitted.
    """
    try:
        with pdfplumber.open(file_path) as pdf:
            text = "\n".join([page.extract_text() or "" for page in pdf.pages])
    except Exception:
        logger.exception("Failed to open PDF for financial parsing")
        return {}

    text_half = _to_half_width(text)
    lines = _normalize_text(text_half)
    multiplier = _detect_unit_multiplier(text_half)
    fiscal_year = fiscal_year_hint or _detect_fiscal_year(lines)

    metrics = _parse_metrics(lines, multiplier)
    if fiscal_year:
        metrics["fiscal_year"] = fiscal_year
    if not metrics:
        logger.warning("No metrics parsed from financial statement: %s", file_path)
    return metrics


def parse_japanese_sme_statement(file_path: str, fiscal_year_hint: Optional[int] = None) -> Dict[str, Optional[int]]:
    """
    Backward-compatible wrapper for existing imports.
    """
    return parse_financial_statement_pdf(file_path, fiscal_year_hint)


__all__ = ["parse_financial_statement_pdf", "parse_japanese_sme_statement"]

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
from app.schemas.company_report import (
    CompanySummary,
    CompanyReportResponse,
    QualitativeBlock,
    RadarPeriod,
    RadarSection,
)
from app.models import Company, CompanyProfile, Conversation, Document, FinancialStatement, HomeworkTask, Message
from app.services import rag as rag_service

logger = logging.getLogger(__name__)

DEMO_USER_ID = os.getenv("DEMO_USER_ID", "demo-user")
DEMO_COMPANY_ID = os.getenv("DEMO_COMPANY_ID", "1")

AXES = ["売上持続性", "収益性", "生産性", "健全性", "効率性", "安全性"]
FALLBACK_TEXT = "LLM未接続のため、簡易コメントを表示しています。"
REPORT_CHAT_MESSAGE_LIMIT = 50
REPORT_HOMEWORK_LIMIT = 15
REPORT_DOCUMENT_SNIPPETS = 6
REPORT_DOCUMENT_QUERY = "経営レポート作成に役立つ情報を要約してください"
SALES_GROWTH_NEUTRAL_SCORE = 2.5
OPERATING_MARGIN_THRESHOLDS = [0.01, 0.03, 0.06, 0.10, 0.15]
PRODUCTIVITY_THRESHOLDS = [5_000_000, 10_000_000, 15_000_000, 20_000_000, 30_000_000]
EQUITY_RATIO_THRESHOLDS = [0.1, 0.2, 0.3, 0.4, 0.5]
ASSET_TURNOVER_THRESHOLDS = [0.5, 1.0, 1.5, 2.0, 3.0]
DEBT_EQUITY_THRESHOLDS = [0.5, 1.0, 2.0, 3.0, 5.0]


@dataclass
class ReportContextPayload:
    company_id: str
    owner_id: Optional[str]
    financial_kpis: Dict[str, Any]
    company_profile: Dict[str, Any]
    chat_messages: List[Dict[str, Any]]
    homeworks: List[Dict[str, Any]]
    documents: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company_id": self.company_id,
            "owner_id": self.owner_id,
            "financial_kpis": self.financial_kpis,
            "company_profile": self.company_profile,
            "chat_messages": self.chat_messages,
            "homeworks": self.homeworks,
            "documents": self.documents,
        }


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(numerator: object, denominator: object) -> Optional[float]:
    num = _to_float(numerator)
    den = _to_float(denominator)
    if num is None or den in (None, 0):
        return None
    try:
        return num / den
    except ZeroDivisionError:
        return None


def _scale_positive(value: Optional[float], thresholds: List[float]) -> float:
    if value is None:
        return 0.0
    score = 0
    for idx, th in enumerate(thresholds, start=1):
        if value >= th:
            score = idx
    return float(min(score, 5))


def _scale_inverse(value: Optional[float], thresholds: List[float]) -> float:
    if value is None:
        return 0.0
    score = 5
    for th in thresholds:
        if value > th:
            score -= 1
    return float(max(min(score, 5), 0))


def _score_sales_growth(growth: Optional[float], has_sales: bool) -> float:
    if not has_sales:
        return 0.0
    if growth is None:
        return SALES_GROWTH_NEUTRAL_SCORE
    if growth < -0.05:
        return 0.0
    if growth < 0:
        return 1.0
    if growth < 0.03:
        return 2.0
    if growth < 0.07:
        return 3.0
    if growth < 0.15:
        return 4.0
    return 5.0


def _score_operating_margin(margin: Optional[float]) -> float:
    return _scale_positive(margin, OPERATING_MARGIN_THRESHOLDS)


def _score_productivity(value: Optional[float]) -> float:
    return _scale_positive(value, PRODUCTIVITY_THRESHOLDS)


def _score_equity_ratio(value: Optional[float]) -> float:
    return _scale_positive(value, EQUITY_RATIO_THRESHOLDS)


def _score_asset_turnover(value: Optional[float]) -> float:
    return _scale_positive(value, ASSET_TURNOVER_THRESHOLDS)


def _score_debt_equity(value: Optional[float]) -> float:
    return _scale_inverse(value, DEBT_EQUITY_THRESHOLDS)


def _resolve_company(db: Session, company_id: str, owner_id: Optional[str] = None) -> Tuple[Company, Optional[CompanyProfile]]:
    profile_user_id = owner_id or (DEMO_USER_ID if company_id == DEMO_COMPANY_ID else None)
    profile = None
    if profile_user_id:
        profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == profile_user_id).first()

    company_filters = [Company.id == company_id, Company.user_id == company_id]
    if profile_user_id:
        company_filters.append(Company.user_id == profile_user_id)
    candidates = db.query(Company).filter(or_(*company_filters)).all()

    if not candidates and profile:
        company = Company(
            id=company_id,
            user_id=profile.user_id,
            company_name=profile.company_name,
            industry=profile.industry,
            employees_range=profile.employees_range,
            annual_sales_range=profile.annual_sales_range,
            annual_revenue_range=profile.annual_revenue_range,
            location_prefecture=profile.location_prefecture,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(company)
        db.commit()
        db.refresh(company)
        return company, profile

    if not candidates:
        fallback_user_id = profile_user_id or company_id
        company = Company(
            id=company_id,
            user_id=fallback_user_id,
            company_name=None,
            industry=None,
            employees_range=None,
            annual_sales_range=None,
            annual_revenue_range=None,
            location_prefecture=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(company)
        db.commit()
        db.refresh(company)
        return company, profile

    def stmt_stats(c: Company) -> tuple[int, int]:
        q = db.query(FinancialStatement).filter(FinancialStatement.company_id == c.id)
        count = q.count()
        latest_year = q.order_by(FinancialStatement.fiscal_year.desc()).first()
        latest_val = latest_year.fiscal_year if latest_year else 0
        return count, latest_val

    chosen = sorted(candidates, key=stmt_stats, reverse=True)[0]
    if not profile and chosen.user_id:
        profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == chosen.user_id).first()
    if profile and chosen.user_id is None:
        chosen.user_id = profile.user_id
        db.commit()
    return chosen, profile


def _load_financials(db: Session, company_id: str) -> List[FinancialStatement]:
    return (
        db.query(FinancialStatement)
        .filter(FinancialStatement.company_id == company_id)
        .order_by(FinancialStatement.fiscal_year.desc())
        .limit(3)
        .all()
    )


def _compute_kpis(stmt: FinancialStatement, prev_sales: Optional[float]) -> Dict[str, Optional[float]]:
    sales = _to_float(stmt.sales)
    ordinary_profit = _to_float(getattr(stmt, "ordinary_profit", None)) or _to_float(stmt.operating_profit)
    employees = _to_float(stmt.employees)
    equity = _to_float(stmt.equity)
    total_liabilities = _to_float(stmt.total_liabilities)
    debt = _to_float(getattr(stmt, "interest_bearing_debt", None)) or _to_float(getattr(stmt, "borrowings", None))
    total_assets = _to_float(getattr(stmt, "total_assets", None))
    current_assets = _to_float(getattr(stmt, "current_assets", None))
    fixed_assets = _to_float(getattr(stmt, "fixed_assets", None))

    if total_assets is None:
        if current_assets is not None or fixed_assets is not None:
            total_assets = (current_assets or 0.0) + (fixed_assets or 0.0)
        elif equity is not None or total_liabilities is not None:
            total_assets = (equity or 0.0) + (total_liabilities or 0.0)

    growth = None
    base_prev_sales = prev_sales or _to_float(getattr(stmt, "previous_sales", None))
    if base_prev_sales not in (None, 0) and sales is not None:
        growth = _safe_div(sales - base_prev_sales, base_prev_sales)

    ordinary_margin = _safe_div(ordinary_profit, sales)
    revenue_per_employee = _safe_div(sales, employees)
    equity_ratio = _safe_div(equity, total_assets)
    asset_turnover = _safe_div(sales, total_assets)
    debt_equity = _safe_div(debt, equity)

    return {
        "sales": sales,
        "growth": growth,
        "ordinary_margin": ordinary_margin,
        "revenue_per_employee": revenue_per_employee,
        "equity_ratio": equity_ratio,
        "asset_turnover": asset_turnover,
        "debt_equity": debt_equity,
    }


def _build_radar(financials: List[FinancialStatement]) -> RadarSection:
    axes = AXES
    periods: List[RadarPeriod] = []
    for idx, stmt in enumerate(financials):
        prev_sales = _to_float(financials[idx + 1].sales) if idx + 1 < len(financials) else None
        kpis = _compute_kpis(stmt, prev_sales)
        raw_values = [
            kpis.get("growth"),
            kpis.get("ordinary_margin"),
            kpis.get("revenue_per_employee"),
            kpis.get("equity_ratio"),
            kpis.get("asset_turnover"),
            kpis.get("debt_equity"),
        ]
        scores = [
            _score_sales_growth(kpis.get("growth"), kpis.get("sales") is not None),
            _score_operating_margin(kpis.get("ordinary_margin")),
            _score_productivity(kpis.get("revenue_per_employee")),
            _score_equity_ratio(kpis.get("equity_ratio")),
            _score_asset_turnover(kpis.get("asset_turnover")),
            _score_debt_equity(kpis.get("debt_equity")),
        ]
        label = f"{stmt.fiscal_year}期" if getattr(stmt, 'fiscal_year', None) else (
            "最新決算期" if idx == 0 else ("前期決算期" if idx == 1 else "前々期決算期")
        )
        periods.append(
            RadarPeriod(
                label=label,
                scores=[float(s) for s in scores],
                raw_values=[(float(v) if v is not None else None) for v in raw_values],
            )
        )
    return RadarSection(axes=axes, periods=periods)


def _load_conversations(db: Session, owner_id: str) -> List[Message]:
    messages = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.user_id == owner_id)
        .order_by(Message.created_at.desc())
        .limit(REPORT_CHAT_MESSAGE_LIMIT)
        .all()
    )
    return list(reversed(messages))


def _load_homeworks(db: Session, owner_id: str) -> List[HomeworkTask]:
    return (
        db.query(HomeworkTask)
        .filter(HomeworkTask.user_id == owner_id)
        .order_by(HomeworkTask.created_at.desc())
        .limit(REPORT_HOMEWORK_LIMIT)
        .all()
    )


def _build_financial_context(radar: RadarSection) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "axes": list(radar.axes),
        "periods": [],
    }
    for period in radar.periods:
        kpis: Dict[str, Dict[str, Optional[float]]] = {}
        for axis, raw, score in zip(radar.axes, period.raw_values, period.scores):
            kpis[axis] = {
                "raw_value": float(raw) if raw is not None else None,
                "score": float(score) if score is not None else None,
            }
        context["periods"].append({"label": period.label, "kpis": kpis})
    return context


def _build_company_profile_context(company: Company, profile: Optional[CompanyProfile]) -> Dict[str, Any]:
    profile_dict: Dict[str, Any] = {
        "company_name": (profile.company_name if profile else None)
        or (profile.name if profile else None)
        or getattr(company, "name", None)
        or company.company_name,
        "industry": (profile.industry if profile else None) or company.industry,
        "employees": (profile.employees if profile else None) or company.employees,
        "employees_range": (profile.employees_range if profile else None) or company.employees_range,
        "annual_sales_range": (profile.annual_sales_range if profile else None) or company.annual_sales_range,
        "annual_revenue_range": (profile.annual_revenue_range if profile else None) or company.annual_revenue_range,
        "location_prefecture": (profile.location_prefecture if profile else None) or company.location_prefecture,
        "years_in_business": profile.years_in_business if profile else None,
        "business_type": profile.business_type if profile else None,
        "founded_year": profile.founded_year if profile else None,
        "city": profile.city if profile else None,
        "main_bank": profile.main_bank if profile else None,
        "has_loan": profile.has_loan if profile else None,
        "has_rent": profile.has_rent if profile else None,
        "owner_age": profile.owner_age if profile else None,
        "main_concern": profile.main_concern if profile else None,
    }
    return {k: v for k, v in profile_dict.items() if v not in (None, "", [])}


def _messages_to_context(messages: List[Message]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.role not in {"user", "assistant"}:
            continue
        content = (msg.content or "").strip()
        if not content:
            continue
        payload.append(
            {
                "role": msg.role,
                "content": content,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
        )
    if len(payload) > REPORT_CHAT_MESSAGE_LIMIT:
        payload = payload[-REPORT_CHAT_MESSAGE_LIMIT:]
    return payload


def _homeworks_to_context(homeworks: List[HomeworkTask]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for task in homeworks:
        items.append(
            {
                "title": task.title,
                "description": task.detail,
                "status": task.status,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "category": task.category,
            }
        )
    return items


def _normalize_snippet_text(text: str, max_length: int = 280) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3].rstrip() + "..."
    return cleaned


def _get_report_documents_summary(db: Session, company: Company, owner_id: Optional[str]) -> List[str]:
    snippets: List[str] = []
    try:
        rag_snippets = asyncio.run(
            rag_service.retrieve_context(
                db=db,
                user_id=owner_id,
                company_id=str(company.id) if company.id else None,
                query=REPORT_DOCUMENT_QUERY,
                top_k=REPORT_DOCUMENT_SNIPPETS,
            )
        )
    except RuntimeError:
        rag_snippets = []
    except Exception:
        logger.exception("Failed to retrieve RAG snippets for report context")
        rag_snippets = []

    for chunk in rag_snippets:
        if not chunk:
            continue
        cleaned = _normalize_snippet_text(chunk)
        if cleaned and cleaned not in snippets:
            snippets.append(cleaned)
        if len(snippets) >= REPORT_DOCUMENT_SNIPPETS:
            return snippets[:REPORT_DOCUMENT_SNIPPETS]

    needed = REPORT_DOCUMENT_SNIPPETS - len(snippets)
    if needed <= 0:
        return snippets[:REPORT_DOCUMENT_SNIPPETS]

    filters = []
    if company.id:
        filters.append(Document.company_id == str(company.id))
    if owner_id:
        filters.append(Document.user_id == owner_id)

    if filters:
        query = db.query(Document).filter(or_(*filters))
        documents = (
            query.order_by(Document.uploaded_at.desc())
            .limit(max(needed, REPORT_DOCUMENT_SNIPPETS))
            .all()
        )
        for doc in documents:
            base_title = doc.filename or "ドキュメント"
            meta_parts: List[str] = []
            if doc.doc_type:
                meta_parts.append(doc.doc_type)
            if doc.period_label:
                meta_parts.append(doc.period_label)
            prefix = base_title
            if meta_parts:
                prefix = f"{base_title} ({' / '.join(meta_parts)})"
            preview = (doc.content_text or "").strip()
            entry = prefix
            if preview:
                entry = f"{prefix}: {_normalize_snippet_text(preview)}"
            if entry not in snippets:
                snippets.append(entry)
            if len(snippets) >= REPORT_DOCUMENT_SNIPPETS:
                break
    return snippets[:REPORT_DOCUMENT_SNIPPETS]


def _build_report_context(
    *,
    company: Company,
    profile: Optional[CompanyProfile],
    radar: RadarSection,
    owner_id: Optional[str],
    messages: List[Message],
    homeworks: List[HomeworkTask],
    document_snippets: List[str],
) -> ReportContextPayload:
    return ReportContextPayload(
        company_id=str(company.id),
        owner_id=owner_id,
        financial_kpis=_build_financial_context(radar),
        company_profile=_build_company_profile_context(company, profile),
        chat_messages=_messages_to_context(messages),
        homeworks=_homeworks_to_context(homeworks),
        documents=document_snippets,
    )


LLM_SYSTEM_PROMPT = """あなたは中小企業診断士です。

入力として以下が与えられます。
- ローカルベンチマークの財務指標（最大3期）
- 会社の基本情報（業種、規模、地域など）
- 経営者とのチャット履歴
- これまでの宿題（対応策メモ）
- 経営者がアップロードした資料の要約

これらを踏まえて、具体的なレポートを日本語でわかりやすくまとめてください。"""


LLM_OUTPUT_GUIDANCE = """出力は以下のJSONスキーマに従ってください。
{
  "qualitative": {
    "keieisha": {
      "summary": "...",
      "risks": "...",
      "strengths": "..."
    },
    "jigyo": { "summary": "..." },
    "kankyo": { "summary": "..." },
    "naibu": { "summary": "..." }
  },
  "current_state": "...",
  "future_goal": "...",
  "action_plan": "...",
  "snapshot_strengths": ["...", "..."],
  "snapshot_weaknesses": ["...", "..."],
  "desired_image": "...",
  "gap_summary": "...",
  "thinking_questions": ["...", "..."]
}
文章量は200〜400字程度とし、箇条書きではなく短い段落で書いてください。"""


def _fallback_report_fields() -> Tuple[
    QualitativeBlock,
    str,
    str,
    str,
    str,
    str,
    List[str],
    List[str],
    List[str],
]:
    return (
        _empty_qualitative(),
        FALLBACK_TEXT,
        FALLBACK_TEXT,
        FALLBACK_TEXT,
        FALLBACK_TEXT,
        FALLBACK_TEXT,
        [],
        [],
        [],
    )


def _generate_report_with_llm(report_context: ReportContextPayload) -> Tuple[
    QualitativeBlock,
    str,
    str,
    str,
    str,
    str,
    List[str],
    List[str],
    List[str],
]:
    user_content = (
        f"{LLM_OUTPUT_GUIDANCE}\n\n"
        f"レポートの材料:\n{json.dumps(report_context.to_dict(), ensure_ascii=False)}"
    )
    try:
        raw = chat_completion_json(
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1400,
        )
    except AzureNotConfiguredError as exc:
        logger.error("Report LLM client init failed. Check Azure/OpenAI env vars.", exc_info=exc)
        return _fallback_report_fields()
    except Exception:
        logger.exception("Report LLM call failed.")
        return _fallback_report_fields()
    return _parse_llm_output(raw)


QUAL_ROWS = {
    "keieisha": [
        ("summary", "経営者・状況"),
        ("risks", "リスク・課題"),
        ("strengths", "強み・活用余地"),
    ],
    "jigyo": [
        ("summary", "事業の特徴・注力分野"),
    ],
    "kankyo": [
        ("summary", "企業を取り巻く環境・関係者"),
    ],
    "naibu": [
        ("summary", "内部管理体制・組織運営"),
    ],
}


def _empty_qualitative() -> QualitativeBlock:
    def block(section: str) -> Dict[str, str]:
        return {label: FALLBACK_TEXT for _, label in QUAL_ROWS[section]}

    return QualitativeBlock(
        keieisha=block("keieisha"),
        jigyo=block("jigyo"),
        kankyo=block("kankyo"),
        naibu=block("naibu"),
    )


def _parse_llm_output(raw: str) -> Tuple[
    QualitativeBlock,
    str,
    str,
    str,
    str,
    str,
    List[str],
    List[str],
    List[str],
]:
    try:
        data = json.loads(raw or "{}")
        qualitative_data = data.get("qualitative", {}) if isinstance(data, dict) else {}

        def pick(section: str) -> Dict[str, str]:
            rows: Dict[str, str] = {}
            template = QUAL_ROWS.get(section, [])
            section_payload = qualitative_data.get(section) if isinstance(qualitative_data, dict) else {}
            payload_dict = section_payload if isinstance(section_payload, dict) else {}
            for key, label in template:
                value = payload_dict.get(key) if key in payload_dict else ""
                text_value = str(value).strip() if value else ""
                rows[label] = text_value or FALLBACK_TEXT
            return rows

        qual = QualitativeBlock(
            keieisha=pick("keieisha"),
            jigyo=pick("jigyo"),
            kankyo=pick("kankyo"),
            naibu=pick("naibu"),
        )
        current_state = str(data.get("current_state") or data.get("current") or "").strip()
        future_goal = str(data.get("future_goal") or data.get("goal") or "").strip()
        action_plan = str(data.get("action_plan") or data.get("plan") or "").strip()
        desired_image = str(data.get("desired_image") or data.get("future_vision") or "").strip()
        gap_summary = str(data.get("gap_summary") or data.get("gap") or "").strip()
        snapshot_strengths = data.get("snapshot_strengths") or data.get("strengths") or []
        snapshot_weaknesses = data.get("snapshot_weaknesses") or data.get("risks_overall") or []
        thinking_questions = data.get("thinking_questions") or []

        if not current_state:
            current_state = FALLBACK_TEXT
        if not future_goal:
            future_goal = FALLBACK_TEXT
        if not action_plan:
            action_plan = FALLBACK_TEXT
        if not desired_image:
            desired_image = FALLBACK_TEXT
        if not gap_summary:
            gap_summary = FALLBACK_TEXT

        def _ensure_list(values: object) -> List[str]:
            if isinstance(values, list):
                return [str(v).strip() for v in values if str(v).strip()]
            if values:
                return [str(values).strip()]
            return []

        snapshot_strengths_list = _ensure_list(snapshot_strengths) or []
        snapshot_weaknesses_list = _ensure_list(snapshot_weaknesses) or []
        thinking_questions_list = _ensure_list(thinking_questions)

        return (
            qual,
            current_state,
            future_goal,
            action_plan,
            desired_image,
            gap_summary,
            thinking_questions_list,
            snapshot_strengths_list,
            snapshot_weaknesses_list,
        )
    except Exception:
        logger.exception("Failed to parse LLM output for qualitative block")
        return _fallback_report_fields()


def build_company_report(db: Session, company_id: str) -> CompanyReportResponse:
    owner_hint = DEMO_USER_ID if company_id == DEMO_COMPANY_ID else None
    company, profile = _resolve_company(db, company_id, owner_hint)
    financials = _load_financials(db, company.id)
    radar = _build_radar(financials) if financials else RadarSection(axes=AXES, periods=[])

    owner_id = profile.user_id if profile else (company.user_id or owner_hint or str(company.id))
    messages = _load_conversations(db, owner_id)
    homeworks = _load_homeworks(db, owner_id)
    document_snippets = _get_report_documents_summary(db, company, owner_id)
    report_context = _build_report_context(
        company=company,
        profile=profile,
        radar=radar,
        owner_id=owner_id,
        messages=messages,
        homeworks=homeworks,
        document_snippets=document_snippets,
    )

    (
        qualitative,
        current_state,
        future_goal,
        action_plan,
        desired_image,
        gap_summary,
        thinking_questions,
        snapshot_strengths,
        snapshot_weaknesses,
    ) = _generate_report_with_llm(report_context)

    company_summary = CompanySummary(
        id=company.id,
        company_name=(profile.company_name if profile else None)
        or (profile.name if profile else None)
        or getattr(company, "name", None)
        or company.company_name,
        name=(profile.name if profile else None) or getattr(company, "name", None) or company.company_name,
        industry=(profile.industry if profile else None) or company.industry,
        employees=(profile.employees if profile else None) or company.employees,
        employees_range=(profile.employees_range if profile else None) or company.employees_range,
        annual_sales_range=(profile.annual_sales_range if profile else None) or company.annual_sales_range,
        annual_revenue_range=(profile.annual_revenue_range if profile else None) or company.annual_revenue_range,
    )

    return CompanyReportResponse(
        company=company_summary,
        radar=radar,
        qualitative=qualitative,
        current_state=current_state,
        future_goal=future_goal,
        action_plan=action_plan,
        snapshot_strengths=snapshot_strengths,
        snapshot_weaknesses=snapshot_weaknesses,
        desired_image=desired_image,
        gap_summary=gap_summary,
        thinking_questions=thinking_questions,
    )

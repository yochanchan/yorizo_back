from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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
from models import Company, CompanyProfile, Conversation, Document, FinancialStatement, HomeworkTask, Message
from services import rag as rag_service

logger = logging.getLogger(__name__)

AXES = ["売上持続性", "収益性", "生産性", "健全性", "効率性", "安全性"]
FALLBACK_TEXT = "LLM未接続のため、簡易コメントを表示しています。"
REPORT_CHAT_MESSAGE_LIMIT = 50
REPORT_HOMEWORK_LIMIT = 15
REPORT_DOCUMENT_SNIPPETS = 6
REPORT_DOCUMENT_QUERY = "経営レポートの作成に役立つ資料内容を要約してください"


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


def _normalize_kpi(key: str, raw: Optional[float]) -> float:
    # Scores must be 0–5; thresholds follow a simple local benchmark style.
    if key == "sales_growth":
        if raw is None:
            return 0
        if raw < 0:
            return 0
        if raw < 0.02:
            return 1
        if raw < 0.05:
            return 2
        if raw < 0.10:
            return 3
        if raw < 0.20:
            return 4
        return 5
    if key == "operating_margin":
        if raw is None:
            return 0
        if raw < 0:
            return 0
        if raw < 0.02:
            return 1
        if raw < 0.05:
            return 2
        if raw < 0.10:
            return 3
        if raw < 0.15:
            return 4
        return 5
    if key == "labor_productivity":
        if raw is None:
            return 0
        if raw < 0:
            return 0
        if raw < 500_000:
            return 1
        if raw < 1_000_000:
            return 2
        if raw < 2_000_000:
            return 3
        if raw < 3_000_000:
            return 4
        return 5
    if key == "ebitda_leverage":
        if raw is None:
            return 0
        if raw <= 0:
            return 5
        if raw <= 1:
            return 4
        if raw <= 3:
            return 3
        if raw <= 5:
            return 2
        return 1
    if key == "owc_months":
        if raw is None:
            return 0
        if raw < 0:
            return 5
        if raw <= 1:
            return 5
        if raw <= 2:
            return 4
        if raw <= 3:
            return 3
        if raw <= 6:
            return 2
        return 1
    if key == "equity_ratio":
        if raw is None:
            return 0
        if raw < 0.1:
            return 0
        if raw < 0.2:
            return 1
        if raw < 0.3:
            return 2
        if raw < 0.4:
            return 3
        if raw < 0.5:
            return 4
        return 5
    return 0.0


def _resolve_company(db: Session, company_id: str) -> Tuple[Company, Optional[CompanyProfile]]:
    candidates = (
        db.query(Company)
        .filter((Company.id == company_id) | (Company.user_id == company_id))
        .all()
    )
    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == company_id)
        .first()
    )
    if not candidates and profile:
        company = Company(
            id=company_id,
            user_id=profile.user_id,
            company_name=profile.company_name,
            industry=profile.industry,
            employees_range=profile.employees_range,
            annual_sales_range=profile.annual_sales_range,
            location_prefecture=profile.location_prefecture,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(company)
        db.commit()
        db.refresh(company)
        return company, profile

    if not candidates:
        # Create a stub company for the requested id so report can still be generated.
        company = Company(
            id=company_id,
            user_id=company_id,
            company_name=None,
            industry=None,
            employees_range=None,
            annual_sales_range=None,
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
        profile = (
            db.query(CompanyProfile)
            .filter(CompanyProfile.user_id == chosen.user_id)
            .first()
        )
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
    operating_profit = _to_float(stmt.operating_profit)
    depreciation = _to_float(stmt.depreciation)
    employees = _to_float(stmt.employees)
    cash = _to_float(getattr(stmt, "cash_and_deposits", None))
    borrowings = _to_float(getattr(stmt, "borrowings", None))
    receivables = _to_float(getattr(stmt, "receivables", None))
    inventory = _to_float(getattr(stmt, "inventory", None))
    payables = _to_float(getattr(stmt, "payables", None))
    equity = _to_float(stmt.equity)
    total_liabilities = _to_float(stmt.total_liabilities)

    ebitda = None
    if operating_profit is not None or depreciation is not None:
        ebitda = (operating_profit or 0.0) + (depreciation or 0.0)

    growth = None
    base_prev_sales = prev_sales or _to_float(getattr(stmt, "previous_sales", None))
    if base_prev_sales:
        growth = _safe_div((sales or 0.0) - base_prev_sales, base_prev_sales)

    owc = None
    if receivables is not None or inventory is not None or payables is not None:
        owc = (receivables or 0.0) + (inventory or 0.0) - (payables or 0.0)
    owc_months = _safe_div(owc, sales) * 12 if owc is not None and sales else None

    equity_ratio = _safe_div(equity, (equity or 0.0) + (total_liabilities or 0.0))
    ebitda_leverage = _safe_div((borrowings or 0.0) - (cash or 0.0), ebitda) if ebitda else None

    return {
        "sales_growth": growth,
        "operating_margin": _safe_div(operating_profit, sales),
        "labor_productivity": _safe_div(operating_profit, employees),
        "ebitda_leverage": ebitda_leverage,
        "owc_months": owc_months,
        "equity_ratio": equity_ratio,
    }


def _build_radar(financials: List[FinancialStatement]) -> RadarSection:
    axes = AXES
    periods: List[RadarPeriod] = []
    prev_sales: Optional[float] = None
    for idx, stmt in enumerate(financials):
        kpis = _compute_kpis(stmt, prev_sales)
        raw_values = [
            kpis.get("sales_growth"),
            kpis.get("operating_margin"),
            kpis.get("labor_productivity"),
            kpis.get("ebitda_leverage"),
            kpis.get("owc_months"),
            kpis.get("equity_ratio"),
        ]
        scores = [
            _normalize_kpi("sales_growth", raw_values[0]),
            _normalize_kpi("operating_margin", raw_values[1]),
            _normalize_kpi("labor_productivity", raw_values[2]),
            _normalize_kpi("ebitda_leverage", raw_values[3]),
            _normalize_kpi("owc_months", raw_values[4]),
            _normalize_kpi("equity_ratio", raw_values[5]),
        ]
        label = "最新決算期" if idx == 0 else ("前期決算期" if idx == 1 else "前々期決算期")
        periods.append(
            RadarPeriod(
                label=label,
                scores=[float(s) for s in scores],
                raw_values=[(float(v) if v is not None else None) for v in raw_values],
            )
        )
        prev_sales = _to_float(stmt.sales)
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
        "company_name": getattr(company, "name", None)
        or company.company_name
        or (profile.company_name if profile else None),
        "industry": company.industry or (profile.industry if profile else None),
        "employees": company.employees or (profile.employees if profile else None),
        "employees_range": company.employees_range or (profile.employees_range if profile else None),
        "annual_sales_range": company.annual_sales_range or (profile.annual_sales_range if profile else None),
        "annual_revenue_range": company.annual_revenue_range or (profile.annual_revenue_range if profile else None),
        "location_prefecture": company.location_prefecture or (profile.location_prefecture if profile else None),
        "years_in_business": profile.years_in_business if profile else None,
    }
    return {k: v for k, v in profile_dict.items() if v not in (None, '', [])}


def _messages_to_context(messages: List[Message]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.role not in {"user", "assistant"}:
            continue
        content = (msg.content or '').strip()
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
    cleaned = ' '.join(text.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3].rstrip() + '...'
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
            base_title = doc.filename or '資料'
            meta_parts: List[str] = []
            if doc.doc_type:
                meta_parts.append(doc.doc_type)
            if doc.period_label:
                meta_parts.append(doc.period_label)
            prefix = base_title
            if meta_parts:
                prefix = f"{base_title} ({' / '.join(meta_parts)})"
            preview = (doc.content_text or '').strip()
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

入力として、
・ローカルベンチマークの財務指標（6指標×最大3期）
・会社の基本情報（業種、規模、地域など）
・経営者とのチャット履歴（相談内容）
・これまでに設定した宿題（対応策のメモ）
・経営者がアップロードした資料（PDFなど）の要約
が与えられます。

これらをすべて踏まえて、
① ローカルベンチマークの「企業の特徴」シートに相当する4領域
   - 経営者
   - 事業
   - 企業を取り巻く環境・関係者
   - 内部管理体制
② 「現状認識」
③ 「将来目標」
④ 「対応策」（宿題の内容も踏まえる）
⑤ 現状スナップショットとしての強み・弱み
⑥ 将来像（desired_image）と現状とのギャップ、経営者が自問するための問い
を、日本語でわかりやすく整理してください。

特に、
- チャット履歴に出てくる「悩み・モヤモヤ・やりたいこと」
- PDFなどの資料に含まれる重要な数字やキーワード
を積極的に反映し、抽象的な一般論ではなく、「この会社の状況」に即したコメントにしてください。
Thinking_questions は経営者自身が次の一手を考えるための問いを2〜3個、簡潔に提示してください。
"""


LLM_OUTPUT_GUIDANCE = """出力は必ず JSON 形式で返してください。
期待するスキーマ:
{
  "qualitative": {
    "keieisha": {
      "summary": "...",
      "risks": "...",
      "strengths": "..."
    },
    "jigyo": {
      "summary": "..."
    },
    "kankyo": {
      "summary": "..."
    },
    "naibu": {
      "summary": "..."
    }
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
各コメントは200〜300文字程度とし、箇条書きではなく短い文章で書いてください。thinking_questions は短い問いを配列で返してください。
"""


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
        f"レポート入力:\n{json.dumps(report_context.to_dict(), ensure_ascii=False)}"
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
        ("summary", "経営者の状況"),
        ("risks", "リスク・課題"),
        ("strengths", "強み・活用資源"),
    ],
    "jigyo": [
        ("summary", "事業の特徴・注力ポイント"),
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
    company, profile = _resolve_company(db, company_id)
    financials = _load_financials(db, company.id)
    radar = _build_radar(financials) if financials else RadarSection(axes=AXES, periods=[])

    owner_id = company.user_id or str(company.id)
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
        name=getattr(company, "name", None) or company.company_name,
        industry=company.industry,
        employees=company.employees,
        annual_revenue_range=company.annual_revenue_range,
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

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
    KPIValue,
    RadarPeriod,
    RadarSection,
)
from app.models import Company, CompanyProfile, Conversation, Document, FinancialStatement, HomeworkTask, Message
from app.services import rag as rag_service

logger = logging.getLogger(__name__)

DEMO_USER_ID = os.getenv("DEMO_USER_ID", "demo-user")
DEMO_COMPANY_ID = os.getenv("DEMO_COMPANY_ID", "1")

AXES = ["売上持続性", "収益性", "健全性", "効率性", "安全性"]
FALLBACK_TEXT = "LLM未接続のため、簡易コメントを表示しています。"
REPORT_CHAT_MESSAGE_LIMIT = 50
REPORT_HOMEWORK_LIMIT = 15
REPORT_DOCUMENT_SNIPPETS = 6
REPORT_DOCUMENT_QUERY = "経営レポート作成に役立つ情報を要約してください"


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


def calc_sales_sustainability(current_sales: Optional[float], prev_sales: Optional[float]) -> Optional[float]:
    """売上持続性[%] = (当期売上 - 前期売上) / 前期売上 * 100（前期が0/Noneなら計算不可）"""
    if current_sales is None or prev_sales is None or prev_sales <= 0:
        return None
    return (current_sales - prev_sales) / prev_sales * 100.0


def calc_profitability(operating_profit: Optional[float], sales: Optional[float]) -> Optional[float]:
    """収益性[%] = 営業利益 / 売上高 * 100（売上0/Noneなら計算不可）"""
    if operating_profit is None or sales is None or sales <= 0:
        return None
    return operating_profit / sales * 100.0


def calc_soundness_years(
    interest_bearing_debt: Optional[float],
    operating_profit: Optional[float],
    depreciation: Optional[float],
    net_income: Optional[float],
) -> Optional[float]:
    """健全性[年] = 借入金残高 / (当期純利益 + 減価償却費)（借入<=0 または CF<=0 は計算不可）"""
    debt = interest_bearing_debt
    if debt is None or debt <= 0:
        return None
    cf = (net_income or 0.0) + (depreciation or 0.0)
    if cf <= 0:
        return None
    return _safe_div(debt, cf)


def calc_working_capital_months(
    receivables: Optional[float],
    inventory: Optional[float],
    payables: Optional[float],
    sales: Optional[float],
) -> Optional[float]:
    """効率性[か月] = (流動資産相当 - 流動負債相当) / 売上 * 12。マイナスは0か月扱い。"""
    if sales is None or sales <= 0:
        return None
    working_capital = (receivables or 0.0) + (inventory or 0.0) - (payables or 0.0)
    months = working_capital / sales * 12.0
    if months < 0:
        months = 0.0
    return months


def calc_equity_ratio_pct(equity: Optional[float], total_assets: Optional[float]) -> Optional[float]:
    """安全性[%] = 自己資本 / 総資産 * 100（クリップなし）"""
    if equity is None or total_assets is None or total_assets <= 0:
        return None
    ratio = equity / total_assets * 100.0
    return ratio


def score_sales_growth(pct: Optional[float]) -> Optional[int]:
    if pct is None:
        return None
    if pct >= 10:
        return 5
    if pct >= 5:
        return 4
    if pct >= 0:
        return 3
    if pct >= -10:
        return 2
    return 1


def score_profit_margin(pct: Optional[float]) -> Optional[int]:
    if pct is None:
        return None
    if pct >= 10:
        return 5
    if pct >= 5:
        return 4
    if pct >= 0:
        return 3
    if pct >= -5:
        return 2
    return 1


def score_debt_years(years: Optional[float], borrowings: Optional[float], ebitda: Optional[float]) -> Optional[int]:
    if years is None:
        return 3  # 中立
    if years <= 0:
        return 5
    if years <= 3:
        return 4
    if years <= 5:
        return 3
    if years <= 7:
        return 2
    return 1


def score_working_capital_months(months: Optional[float]) -> Optional[int]:
    if months is None:
        return 3  # 中立
    if months <= 1:
        return 5
    if months <= 2:
        return 4
    if months <= 3:
        return 3
    if months <= 4:
        return 2
    return 1


def score_equity_ratio(pct: Optional[float]) -> Optional[int]:
    if pct is None:
        return 3  # 中立
    if pct >= 30:
        return 5
    if pct >= 20:
        return 4
    if pct >= 10:
        return 3
    if pct >= 0:
        return 2
    return 1


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


def _compute_kpis(stmt: FinancialStatement, prev_sales: Optional[float]) -> List[Dict[str, Any]]:
    sales = _to_float(stmt.sales)
    previous_sales = prev_sales if prev_sales is not None else _to_float(getattr(stmt, "previous_sales", None))
    operating_profit = _to_float(getattr(stmt, "operating_profit", None))
    depreciation = _to_float(getattr(stmt, "depreciation", None))
    net_income = _to_float(getattr(stmt, "net_income", None))
    borrowings = _to_float(getattr(stmt, "interest_bearing_debt", None))
    if borrowings is None:
        borrowings = _to_float(getattr(stmt, "borrowings", None))
    receivables = _to_float(getattr(stmt, "receivables", None))
    inventory = _to_float(getattr(stmt, "inventory", None))
    payables = _to_float(getattr(stmt, "payables", None))
    total_assets = _to_float(getattr(stmt, "total_assets", None))
    equity = _to_float(getattr(stmt, "equity", None))

    raw_sales_growth = calc_sales_sustainability(sales, previous_sales)
    raw_profitability = calc_profitability(operating_profit, sales)
    raw_soundness = calc_soundness_years(borrowings, operating_profit, depreciation, net_income)
    raw_efficiency = calc_working_capital_months(receivables, inventory, payables, sales)
    raw_safety = calc_equity_ratio_pct(equity, total_assets)

    def _display(val: Optional[float], unit: str) -> str:
        if val is None:
            return "データなし"
        rounded = round(val * 10) / 10
        return f"{rounded:.1f}{unit}"

    kpis: List[Dict[str, Any]] = [
        {
            "key": "sales_sustainability",
            "label": AXES[0],
            "raw": raw_sales_growth,
            "value_display": _display(raw_sales_growth, "%"),
            "unit": "%",
            "score": score_sales_growth(raw_sales_growth),
        },
        {
            "key": "profitability",
            "label": AXES[1],
            "raw": raw_profitability,
            "value_display": _display(raw_profitability, "%"),
            "unit": "%",
            "score": score_profit_margin(raw_profitability),
        },
        {
            "key": "soundness",
            "label": AXES[2],
            "raw": raw_soundness,
            "value_display": _display(raw_soundness, "年"),
            "unit": "年",
            "score": score_debt_years(raw_soundness, borrowings, (net_income or 0) + (depreciation or 0)),
        },
        {
            "key": "efficiency",
            "label": AXES[3],
            "raw": raw_efficiency,
            "value_display": _display(raw_efficiency, "か月"),
            "unit": "か月",
            "score": score_working_capital_months(raw_efficiency),
        },
        {
            "key": "safety",
            "label": AXES[4],
            "raw": raw_safety,
            "value_display": _display(raw_safety, "%"),
            "unit": "%",
            "score": score_equity_ratio(raw_safety),
        },
    ]
    return kpis


def _build_radar(financials: List[FinancialStatement]) -> RadarSection:
    axes = AXES
    periods: List[RadarPeriod] = []
    for idx, stmt in enumerate(financials):
        prev_sales = _to_float(financials[idx + 1].sales) if idx + 1 < len(financials) else None
        kpis = _compute_kpis(stmt, prev_sales)
        raw_values = [k.get("raw") for k in kpis]
        scores = [k.get("score") for k in kpis]
        fiscal_year = getattr(stmt, "fiscal_year", None)
        if fiscal_year:
            label = f"{fiscal_year}期"
        else:
            label = "最新期" if idx == 0 else ("前期" if idx == 1 else "前々期")
        periods.append(
            RadarPeriod(
                label=label,
                scores=[float(s) if s is not None else 0.0 for s in scores],
                raw_values=[(float(v) if v is not None else None) for v in raw_values],
                kpis=[KPIValue(**k) for k in kpis],
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
        if getattr(period, "kpis", None):
            for item in period.kpis:
                kpis[item.label] = {
                    "raw_value": item.raw,
                    "score": item.score,
                    "unit": item.unit,
                    "display": item.value_display,
                }
        else:
            for axis, raw, score in zip(radar.axes, period.raw_values, period.scores):
                kpis[axis] = {
                    "raw_value": float(raw) if raw is not None else None,
                    "score": float(score) if score is not None else None,
                    "unit": None,
                }
        for axis, raw, score in zip(radar.axes, period.raw_values, period.scores):
            kpis[axis] = {
                "raw_value": kpis.get(axis, {}).get("raw_value", float(raw) if raw is not None else None),
                "score": kpis.get(axis, {}).get("score", float(score) if score is not None else None),
                "unit": kpis.get(axis, {}).get("unit"),
                "display": kpis.get(axis, {}).get("display"),
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


LLM_SYSTEM_PROMPT = """あなたは中小企業診断士です。以下の情報を踏まえて、日本語で簡潔にレポートをまとめてください。

入力として以下が与えられます:
- ローカルベンチマークの財務指標（最大3期）
- 会社の基本情報（業種、規模、地域など）
- 経営者とのチャット履歴
- これまでの宿題（対応策メモ）
- 経営者がアップロードした資料の要約

これらを踏まえて、簡潔で分かりやすいレポートを出力してください。"""


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
"""
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
        f"繝ｬ繝昴・繝医・譚先侭:\n{json.dumps(report_context.to_dict(), ensure_ascii=False)}"
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
        ("summary", "経営者の特徴"),
        ("risks", "リスク"),
        ("strengths", "強み"),
    ],
    "jigyo": [
        ("summary", "事業・商品/サービス"),
    ],
    "kankyo": [
        ("summary", "外部環境"),
    ],
    "naibu": [
        ("summary", "内部・組織"),
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
    """
    Return default Japanese texts when the LLM is not available.

    The tuple shape must match `_generate_report_with_llm` unpacking in `build_company_report`.
    """
    fallback_summary = (
        "LLM未接続のため、自動要約はまだ利用できませんが、チャット内容や決算書をもとに相談員と一緒に現状を整理してください。"
    )
    fallback_strengths = "強みの自動整理は未実装です。これまでうまくいっている点や顧客に評価されている点をメモしておきましょう。"
    fallback_risks = "リスクの自動整理は未実装です。売上の波や資金繰りで不安な点があれば相談メモに記録してください。"
    fallback_next_steps = "次の一歩の自動提案は未実装です。気になるテーマを1〜3個決めて、よろず支援拠点で相談してみましょう。"
    fallback_radar_comment = "レーダーチャートは決算書の数値をもとに概況を示しています。詳細なコメントは相談員と一緒に確認してください。"
    fallback_kpi_comment = "各指標の見方や目安は、画面の説明と相談員からのアドバイスを参考にしてください。"

    return (
        _empty_qualitative(),
        fallback_summary,  # current_state
        fallback_summary,  # future_goal
        fallback_next_steps,  # action_plan
        fallback_radar_comment,  # desired_image
        fallback_kpi_comment,  # gap_summary
        [],  # thinking_questions
        [fallback_strengths],  # snapshot_strengths
        [fallback_risks],  # snapshot_weaknesses
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




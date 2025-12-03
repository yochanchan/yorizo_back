from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
from app.schemas.company_report import (
    CompanySummary,
    CompanyReportResponse,
    QualitativeBlock,
    RadarPeriod,
    RadarSection,
)
from models import Company, CompanyProfile, Conversation, FinancialStatement, HomeworkTask, Message

logger = logging.getLogger(__name__)

AXES = ["売上持続性", "収益性", "生産性", "健全性", "効率性", "安全性"]


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
        .limit(40)
        .all()
    )
    return list(reversed(messages))


def _load_homeworks(db: Session, owner_id: str) -> List[HomeworkTask]:
    return (
        db.query(HomeworkTask)
        .filter(HomeworkTask.user_id == owner_id)
        .order_by(HomeworkTask.created_at.desc())
        .limit(20)
        .all()
    )


def _build_llm_prompt(
    radar: RadarSection,
    messages: List[Message],
    homeworks: List[HomeworkTask],
    profile: Optional[CompanyProfile],
) -> str:
    kpi_dict: Dict[str, float] = {}
    if radar.periods:
        latest = radar.periods[0]
        for axis, raw in zip(radar.axes, latest.raw_values):
            if raw is not None:
                kpi_dict[axis] = raw

    chat_messages = [
        {"role": m.role, "content": m.content}
        for m in messages
        if m.role in {"user", "assistant"} and m.content
    ][:50]

    hw_list = [
        {"title": h.title, "detail": h.detail, "status": h.status}
        for h in homeworks
    ][:5]

    profile_dict: Dict[str, object] = {}
    if profile:
        profile_dict = {
            "company_name": profile.company_name,
            "industry": profile.industry,
            "employees_range": profile.employees_range,
            "annual_sales_range": profile.annual_sales_range,
            "location_prefecture": profile.location_prefecture,
            "years_in_business": profile.years_in_business,
        }

    user_payload = {
        "financial_kpis": kpi_dict,
        "chat_messages": chat_messages,
        "homeworks": hw_list,
        "company_profile": profile_dict,
    }

    system_prompt = (
        "あなたは中小企業診断士です。\n"
        "ローカルベンチマークの「企業の特徴」シートの様式に沿って、\n"
        "チャットで経営者が話した内容をもとに、定性的な診断コメントを作成してください。\n\n"
        "評価対象は以下の4つです：\n"
        "①経営者（理念・ビジョン、経営意欲、後継者、ターンングポイントなど）\n"
        "②事業（強み、弱み、販路、技術、IT活用、付加価値向上施策）\n"
        "③企業を取り巻く環境・関係者（市場動向、顧客、競合、取引金融機関）\n"
        "④内部管理体制（組織、人材育成、品質管理、経営計画、情報管理）\n\n"
        "また、「現状認識」「将来目標」「対応策」もチャット内容から抽出して作成してください。\n"
        "出力は必ず JSON で返し、各項目は200〜300文字程度の簡潔な記述にしてください。"
    )

    user_prompt = (
        "以下を JSON で返してください：\n"
        '{\n  "qualitative": { "keieisha": {...}, "jigyo": {...}, "kankyo": {...}, "naibu": {...} },\n'
        '  "current_state": "...",\n  "future_goal": "...",\n  "action_plan": "..." \n}\n'
        f"入力データ:\n{json.dumps(user_payload, ensure_ascii=False)}"
    )

    return system_prompt, user_prompt


QUAL_ROWS = {
    "keieisha": [
        "経営理念・ビジョン／経営哲学・考え方等",
        "経営意欲 ※成長志向・現状維持など",
        "後継者の有無",
        "承継のタイミング・関係",
        "企業及び事業構造 ※ターニングポイントの把握",
    ],
    "jigyo": [
        "強み（技術力・販売力等）",
        "弱み（技術力・販売力等）",
        "ITに関する状況・活用の状況",
        "1時間当たり付加価値額（生産性）向上に向けた取組み",
    ],
    "kankyo": [
        "市場動向・規模・シェアの把握／競合他社との比較",
        "需要リピート率・新規顧客率・主な取引先企業の推移・顧客からのフィードバックの有無",
        "従業員定着率・勤続年数・平均給与",
        "取引金融機関数・業態／メインバンクとの関係",
    ],
    "naibu": [
        "組織体制／品質管理・情報管理体制",
        "事業計画・経営計画の有無／従業員との共有状況／社内会議の実施状況",
        "研究開発・商品開発の体制／知的財産権の保有・活用状況",
        "人材育成の取組み状況／人材育成の仕組み",
    ],
}


def _empty_qualitative() -> QualitativeBlock:
    def block(keys: List[str]) -> Dict[str, str]:
        return {k: "LLM未接続のため、簡易コメントを表示しています。" for k in keys}

    return QualitativeBlock(
        keieisha=block(QUAL_ROWS["keieisha"]),
        jigyo=block(QUAL_ROWS["jigyo"]),
        kankyo=block(QUAL_ROWS["kankyo"]),
        naibu=block(QUAL_ROWS["naibu"]),
    )


def _parse_llm_output(raw: str) -> Tuple[QualitativeBlock, str, str, str]:
    try:
        data = json.loads(raw or "{}")
        def pick(section: str) -> Dict[str, str]:
            base = {k: "" for k in QUAL_ROWS[section]}
            qual_data = data.get("qualitative", {}) if isinstance(data, dict) else {}
            section_data = qual_data.get(section) if isinstance(qual_data, dict) else {}
            if isinstance(section_data, dict):
                for k in base:
                    if k in section_data:
                        base[k] = str(section_data[k])
            return base

        qual = QualitativeBlock(
            keieisha=pick("keieisha"),
            jigyo=pick("jigyo"),
            kankyo=pick("kankyo"),
            naibu=pick("naibu"),
        )
        return (
            qual,
            str(data.get("current_state") or data.get("current") or ""),
            str(data.get("future_goal") or data.get("goal") or ""),
            str(data.get("action_plan") or data.get("plan") or ""),
        )
    except Exception:
        logger.exception("Failed to parse LLM output for qualitative block")
        qual = _empty_qualitative()
        return qual, "LLM未接続のため、簡易コメントを表示しています。", "LLM未接続のため、簡易コメントを表示しています。", "LLM未接続のため、簡易コメントを表示しています。"


def build_company_report(db: Session, company_id: str) -> CompanyReportResponse:
    company, profile = _resolve_company(db, company_id)
    financials = _load_financials(db, company.id)
    radar = _build_radar(financials) if financials else RadarSection(axes=AXES, periods=[])

    owner_id = company.user_id or company.id
    messages = _load_conversations(db, owner_id)
    homeworks = _load_homeworks(db, owner_id)

    qualitative = _empty_qualitative()
    current_state = "LLM未接続のため、簡易コメントを表示しています。"
    future_goal = "LLM未接続のため、簡易コメントを表示しています。"
    action_plan = "LLM未接続のため、簡易コメントを表示しています。"

    system_prompt, user_prompt = _build_llm_prompt(radar, messages, homeworks, profile)
    try:
        raw = chat_completion_json(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            max_tokens=900,
        )
        qualitative, current_state, future_goal, action_plan = _parse_llm_output(raw)
    except AzureNotConfiguredError:
        pass
    except Exception:
        logger.exception("LLM generation failed; using fallback")

    company_summary = CompanySummary(
        id=company.id,
        name=company.name or company.company_name,
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
    )

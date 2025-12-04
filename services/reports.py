from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.schemas.reports import (
    CompanyAnalysisCategory,
    CompanyAnalysisReport,
    LocalBenchmark,
    LocalBenchmarkAxis,
    LocalBenchmarkScore,
)
from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
from models import CompanyProfile, Document, Message
from services.company_report import build_company_report


def _scale_positive(value: Optional[float], thresholds: List[float]) -> int:
    """
    Convert a numeric value into a 1-5 score using ascending thresholds.
    thresholds: list of four breakpoints for 2,3,4,5 boundaries.
    """
    if value is None:
        return 3
    score = 1
    for idx, th in enumerate(thresholds, start=2):
        if value >= th:
            score = idx
    return min(score, 5)


def _scale_inverse(value: Optional[float], thresholds: List[float]) -> int:
    """
    Lower is better. thresholds ascending; below first ->5, above last ->1.
    """
    if value is None:
        return 3
    score = 5
    for idx, th in enumerate(thresholds, start=1):
        if value > th:
            score = 5 - idx
    return max(min(score, 5), 1)


def _scale_0_100(score_1_to_5: int) -> int:
    return max(0, min(100, score_1_to_5 * 20))


def _build_local_benchmark_axes(kpis: Dict[str, float]) -> List[LocalBenchmarkAxis]:
    axes: List[LocalBenchmarkAxis] = []
    profit_score = _scale_positive(kpis.get("operating_margin"), [0, 0.03, 0.06, 0.1])
    prod_score = _scale_positive(kpis.get("labor_productivity"), [500000, 1000000, 1500000, 2000000])
    stability_score = max(
        _scale_positive(kpis.get("equity_ratio"), [0.1, 0.2, 0.4, 0.6]),
        _scale_inverse(kpis.get("ebitda_debt_ratio"), [6, 4, 2, 1]),
    )
    growth_score = _scale_positive(kpis.get("sales_growth_rate"), [-0.05, 0, 0.05, 0.1])

    axes.append(LocalBenchmarkAxis(id="profitability", label="収益性", score=_scale_0_100(profit_score), reason="営業利益率から評価"))
    axes.append(LocalBenchmarkAxis(id="productivity", label="生産性", score=_scale_0_100(prod_score), reason="労働生産性から評価"))
    axes.append(LocalBenchmarkAxis(id="stability", label="安定性", score=_scale_0_100(stability_score), reason="自己資本比率と負債バランスから評価"))
    axes.append(LocalBenchmarkAxis(id="growth", label="成長性", score=_scale_0_100(growth_score), reason="売上増加率から評価"))
    return axes


def _finance_scores(kpis: Dict[str, float]) -> List[LocalBenchmarkScore]:
    label_map = {
        "sales_growth_rate": ("売上増加率", "前年比の売上成長率"),
        "operating_margin": ("営業利益率", "売上に占める営業利益の割合"),
        "labor_productivity": ("労働生産性", "従業員1人あたりの営業利益"),
        "ebitda_debt_ratio": ("EBITDA有利子負債倍率", "借入金とキャッシュのバランス"),
        "operating_working_capital_period": ("営業運転資本回転期間（月）", "資金の回転期間"),
        "equity_ratio": ("自己資本比率", "財務健全性の指標"),
    }
    scores: List[LocalBenchmarkScore] = []
    for key, (label, desc) in label_map.items():
        val = kpis.get(key)
        score_val = None
        if key == "equity_ratio":
            score_val = _scale_positive(val, [0.1, 0.2, 0.4, 0.6])
        elif key == "operating_margin":
            score_val = _scale_positive(val, [0, 0.03, 0.06, 0.1])
        elif key == "sales_growth_rate":
            score_val = _scale_positive(val, [-0.05, 0, 0.05, 0.1])
        elif key == "labor_productivity":
            score_val = _scale_positive(val, [500000, 1000000, 1500000, 2000000])
        elif key == "ebitda_debt_ratio":
            score_val = _scale_inverse(val, [6, 4, 2, 1])
        elif key == "operating_working_capital_period":
            score_val = _scale_inverse(val, [8, 6, 4, 2])
        scores.append(
            LocalBenchmarkScore(
                label=label,
                description=desc,
                score=score_val,
                raw_value=val,
                reason=None,
            )
        )
    return scores


def _strengths_weaknesses(kpis: Dict[str, float]) -> (List[str], List[str]):
    strengths: List[str] = []
    weaknesses: List[str] = []
    if kpis.get("equity_ratio") and kpis["equity_ratio"] >= 0.4:
        strengths.append("自己資本比率が比較的高く、安定性があります。")
    if kpis.get("operating_margin") and kpis["operating_margin"] >= 0.08:
        strengths.append("営業利益率が良好です。")
    if kpis.get("sales_growth_rate") and kpis["sales_growth_rate"] > 0.05:
        strengths.append("売上が伸びています。")

    if kpis.get("equity_ratio") and kpis["equity_ratio"] < 0.2:
        weaknesses.append("自己資本比率が低く、財務体力に課題があります。")
    if kpis.get("ebitda_debt_ratio") and kpis["ebitda_debt_ratio"] > 4:
        weaknesses.append("借入金依存度が高めです。")
    if kpis.get("sales_growth_rate") is not None and kpis["sales_growth_rate"] < 0:
        weaknesses.append("売上が減少傾向です。")
    return strengths, weaknesses


def _pain_points_from_topics(topics: List[str]) -> List[CompanyAnalysisCategory]:
    if not topics:
        return [CompanyAnalysisCategory(category="最近の気になること", items=["最近の気になることはまだ整理されていません。"])]
    return [CompanyAnalysisCategory(category="最近の気になること", items=topics)]


def _llm_summary(kpis: Dict[str, float], concerns: List[str]) -> str:
    if not kpis and not concerns:
        return "最新の会話と決算データをまとめています。"
    prompt = (
        "あなたは中小企業診断士です。以下のKPIと最近の相談テーマを踏まえて、会社の現状を1-2文で日本語でまとめてください。\n"
        f"KPI: {json.dumps(kpis, ensure_ascii=False)}\n"
        f"相談テーマ: {json.dumps(concerns, ensure_ascii=False)}"
    )
    try:
        resp = chat_completion_json(
            messages=[{"role": "system", "content": "日本語で短くまとめてください。JSONは不要です。"}, {"role": "user", "content": prompt}],
            max_tokens=200,
        )
        if resp:
            return resp.strip()
    except AzureNotConfiguredError:
        return "LLM未接続のため、簡易コメントを表示しています。"
    except Exception:
        return "最新の情報を整理しています。"
    return "最新の情報を整理しています。"


def _score_entry(
    *,
    key: str,
    label: str,
    raw: Optional[float],
    reason: str,
    industry_avg: Optional[float] = None,
    not_enough_data: bool = False,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "raw": raw,
        "industry_avg": industry_avg,
        "reason": reason,
        "not_enough_data": not_enough_data,
    }


def build_finance_section(
    *,
    profile: Optional[CompanyProfile],
    documents: Sequence[Document],
    conversation_count: int,
    pending_homework_count: int,
) -> Optional[Dict[str, Any]]:
    doc_count = len(list(documents))
    financial_docs = [doc for doc in documents if (doc.doc_type or "").startswith("financial")]
    has_profile = profile is not None

    if not has_profile and doc_count == 0:
        return None

    overview_parts: List[str] = []
    if has_profile and profile.company_name:
        overview_parts.append(f"{profile.company_name}の登録情報を基にしています。")
    if doc_count:
        overview_parts.append(f"{doc_count}件の資料（決算・試算表など）を参照しました。")
    else:
        overview_parts.append("まだ決算資料がアップロードされていないため、登録情報と会話履歴から推測しています。")
    if pending_homework_count:
        overview_parts.append(f"宿題は{pending_homework_count}件が未完了です。")
    overview_comment = " ".join(overview_parts)

    scores: List[Dict[str, Any]] = []
    scores.append(
        _score_entry(
            key="documents_registered",
            label="財務資料の登録状況",
            raw=float(doc_count),
            reason="アップロード済みの資料数を基に算出。",
            not_enough_data=doc_count == 0,
        )
    )
    profile_fields = [
        profile.company_name if profile else None,
        profile.industry if profile else None,
        profile.annual_sales_range if profile else None,
        profile.employees_range if profile else None,
    ]
    profile_score = sum(1 for item in profile_fields if item) / max(len(profile_fields), 1)
    scores.append(
        _score_entry(
            key="profile_completeness",
            label="会社プロフィールの充実度",
            raw=round(profile_score * 100, 1) if has_profile else None,
            reason="会社名・業種・規模などの登録状況を基に評価。",
            not_enough_data=not has_profile,
        )
    )
    scores.append(
        _score_entry(
            key="conversation_frequency",
            label="最近の相談頻度",
            raw=float(conversation_count),
            reason="Yorizoとの対話回数が多いほど課題が整理されています。",
        )
    )
    scores.append(
        _score_entry(
            key="homework_followup",
            label="宿題フォロー状況",
            raw=float(pending_homework_count),
            reason="未完了の宿題数を確認。",
            not_enough_data=False,
        )
    )
    scores.append(
        _score_entry(
            key="financial_coverage",
            label="決算データの網羅度",
            raw=float(len(financial_docs)),
            reason="決算書タイプの資料がどれだけ揃っているかを確認。",
            not_enough_data=len(financial_docs) == 0,
        )
    )

    return {
        "overview_comment": overview_comment,
        "scores": scores,
    }


def _conversation_tail(text: str, max_chars: int = 2500) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def generate_concerns(
    *,
    conversation_text: str,
    main_concern: Optional[str],
    documents_summary: Sequence[str],
) -> List[str]:
    system_prompt = "あなたは中小企業診断士です。入力を読み、経営者が気にしている課題を日本語で整理してください。"
    payload = {
        "main_concern": main_concern,
        "conversation_excerpt": _conversation_tail(conversation_text or ""),
        "documents": list(documents_summary),
    }
    user_prompt = (
        "以下の情報から、経営者が抱えている課題やモヤモヤを日本語の短い文章で3件以内まとめ、必ずJSON配列で返してください。\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    raw = chat_completion_json(
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=400,
    )
    data = json.loads(raw or "[]")
    if isinstance(data, dict) and "concerns" in data:
        data = data["concerns"]
    if not isinstance(data, list):
        raise ValueError("concerns response must be a list")
    concerns = [str(item).strip() for item in data if str(item).strip()]
    if not concerns:
        raise ValueError("concerns response was empty")
    return concerns


def fallback_concerns(messages: Sequence[Message]) -> List[str]:
    user_lines = [msg.content for msg in messages if getattr(msg, "role", "") == "user" and msg.content]
    tail = [line.strip() for line in user_lines[-3:] if line.strip()]
    if tail:
        return tail
    return ["最近の相談内容を整理しています。"]


def generate_hints(
    *,
    main_concern: Optional[str],
    concerns: Sequence[str],
    finance_section: Optional[Any],
    documents_summary: Sequence[str],
    profile: Optional[CompanyProfile],
) -> List[str]:
    system_prompt = "あなたは中小企業診断士です。経営者に提案できる具体的なアドバイスをまとめてください。"
    finance_context: Dict[str, Any] = {}
    if finance_section:
        finance_context = {
            "overview": getattr(finance_section, "overview_comment", None) if not isinstance(finance_section, dict) else finance_section.get("overview_comment"),
            "scores": getattr(finance_section, "scores", None) if not isinstance(finance_section, dict) else finance_section.get("scores"),
        }
    payload = {
        "main_concern": main_concern,
        "concerns": list(concerns),
        "documents": list(documents_summary),
        "finance": finance_context,
        "profile": {
            "industry": profile.industry if profile else None,
            "annual_sales_range": profile.annual_sales_range if profile else None,
            "location": profile.location_prefecture if profile else None,
        },
    }
    user_prompt = (
        "以下の情報を踏まえて、経営者への提案や次の打ち手を日本語で3件以内列挙してください。必ずJSON配列で返してください。\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    raw = chat_completion_json(
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=400,
    )
    data = json.loads(raw or "[]")
    if isinstance(data, dict) and "hints" in data:
        data = data["hints"]
    if not isinstance(data, list):
        raise ValueError("hints response must be a list")
    hints = [str(item).strip() for item in data if str(item).strip()]
    if not hints:
        raise ValueError("hints response was empty")
    return hints


def fallback_hints() -> List[str]:
    return [
        "最新の会話と宿題の内容をもとに、次回までに確認したい論点を整理してください。",
        "決算資料をアップロードすると、より精度の高いアドバイスが可能になります。",
    ]


def build_company_analysis_report(db: Session, company_id: str) -> CompanyAnalysisReport:
    report = build_company_report(db, company_id)
    kpi_values: Dict[str, float] = {}
    if report.radar.periods:
        latest = report.radar.periods[0]
        for axis, raw in zip(report.radar.axes, latest.raw_values):
            kpi_values[axis] = raw

    axes = _build_local_benchmark_axes(kpi_values) if kpi_values else []
    finance_scores = _finance_scores(kpi_values)
    strengths, weaknesses = _strengths_weaknesses(kpi_values)

    pain_points = _pain_points_from_topics(list(report.qualitative.keieisha.values()))
    summary_text = report.current_state or "最新の情報を整理しています。"
    basic_info_note = report.action_plan or ""

    action_items = [report.action_plan] if report.action_plan else []

    local_benchmark = LocalBenchmark(axes=axes)

    return CompanyAnalysisReport(
        company_id=company_id,
        last_updated_at=datetime.utcnow(),
        summary=summary_text,
        basic_info_note=basic_info_note,
        finance_scores=finance_scores,
        pain_points=pain_points,
        strengths=strengths or ["強みはこれから整理します。"],
        weaknesses=weaknesses or ["弱みの整理はこれからです。"],
        action_items=action_items or ["宿題は未登録です。"],
        local_benchmark=local_benchmark,
    )

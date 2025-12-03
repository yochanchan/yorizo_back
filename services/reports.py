from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.schemas.reports import (
    CompanyAnalysisCategory,
    CompanyAnalysisReport,
    LocalBenchmark,
    LocalBenchmarkAxis,
    LocalBenchmarkScore,
)
from app.core.openai_client import AzureNotConfiguredError, chat_completion_json
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

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.schemas.reports import (
    CompanyAnalysisCategory,
    CompanyAnalysisReport,
    LocalBenchmark,
    LocalBenchmarkAxis,
    LocalBenchmarkScore,
)
from app.core.openai_client import AzureNotConfiguredError, LlmError, LlmResult, chat_completion_json
from app.models import CompanyProfile, Conversation, Document, HomeworkTask, HomeworkStatus, Message
from app.services.company_report import build_company_report

logger = logging.getLogger(__name__)


def _chat_json_result(
    prompt_id: str,
    messages: Sequence[Dict[str, Any]],
    *,
    max_tokens: int | None = None,
) -> LlmResult[Any]:
    try:
        raw = chat_completion_json(messages=messages, max_tokens=max_tokens)
        data = json.loads(raw or "{}")
        return LlmResult(ok=True, value=data)
    except AzureNotConfiguredError as exc:
        return LlmResult(ok=False, error=LlmError(code="not_configured", message=str(exc)))
    except HTTPException as exc:
        retryable = exc.status_code >= 500
        return LlmResult(
            ok=False,
            error=LlmError(code="upstream_http_error", message=str(exc.detail), retryable=retryable),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM json generation failed for %s: %s", prompt_id, exc)
        return LlmResult(ok=False, error=LlmError(code="bad_json", message=str(exc), retryable=False))


def _scale_positive(value: Optional[float], thresholds: List[float]) -> int:
    if value is None:
        return 3
    score = 1
    for idx, th in enumerate(thresholds, start=2):
        if value >= th:
            score = idx
    return min(score, 5)


def _scale_inverse(value: Optional[float], thresholds: List[float]) -> int:
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
        "equity_ratio": ("自己資本比率", "財務健全性の目安"),
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
        "あなたは中小企業診断士です。以下のKPIと最近の相談テーマを踏まえて、会社の現状を1-2文でまとめてください。\n"
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
        overview_parts.append(f"{doc_count}件の資料（決算書や試算表など）を参照しました。")
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
            reason="アップロード済みの資料数を基に算定。",
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
            label="会社プロフィールの充足度",
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
    history_messages: Sequence[Message],
) -> List[str]:
    system_prompt = "あなたは中小企業診断士です。入力を読み、経営者が気にしている課題を日本語で整理してください。"
    payload = {
        "main_concern": main_concern,
        "conversation_excerpt": _conversation_tail(conversation_text or ""),
        "documents": list(documents_summary),
    }
    user_prompt = (
        "以下の情報から、経営者が抱えている課題やモヤモヤを日本語で短い文章で3件以内でまとめ、必ずJSON配列で返してください。\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    result = _chat_json_result(
        "LLM-REPORT-01-v1",
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=400,
    )
    data = result.value if result.ok else None
    if isinstance(data, dict) and "concerns" in data:
        data = data["concerns"]
    if not isinstance(data, list):
        if not result.ok:
            logger.warning("concerns fallback triggered: %s", result.error)
        return fallback_concerns(history_messages)
    concerns = [str(item).strip() for item in data if str(item).strip()]
    if not concerns:
        return fallback_concerns(history_messages)
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
        "以下の情報を踏まえて、経営者への提案や次の打ち手を日本語で3件以内で挙げてください。必ずJSON配列で返してください。\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    result = _chat_json_result(
        "LLM-REPORT-01-v1",
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=400,
    )
    data = result.value if result.ok else None
    if isinstance(data, dict) and "hints" in data:
        data = data["hints"]
    if not isinstance(data, list):
        if not result.ok:
            logger.warning("hints fallback triggered: %s", result.error)
        return fallback_hints()
    hints = [str(item).strip() for item in data if str(item).strip()]
    if not hints:
        return fallback_hints()
    return hints


def fallback_hints() -> List[str]:
    return [
        "最新の会話と宿題の結果をもとに、次回までに確認したい論点を整理してください。",
        "決算資料をアップロードすると、より精度の高いアドバイスが可能になります。",
    ]


def _format_period(messages: List[Message], conversation: Conversation) -> str:
    if messages:
        start = messages[0].created_at or conversation.started_at or datetime.utcnow()
        end = messages[-1].created_at or conversation.started_at or datetime.utcnow()
    else:
        start = conversation.started_at or datetime.utcnow()
        end = conversation.started_at or datetime.utcnow()
    start_label = f"{start.year}年{start.month}月"
    end_label = f"{end.year}年{end.month}月"
    if start_label == end_label:
        return f"{start_label}のチャット相談"
    return f"{start_label}〜{end_label}に実施したチャット相談"


def _build_sources(profile: Optional[CompanyProfile], documents: List[Document], messages: List[Message]) -> List[str]:
    sources: List[str] = []
    if messages:
        sources.append("チャット相談の履歴")
    if profile:
        sources.append("会社プロフィールの登録情報")
    for doc in documents:
        title = getattr(doc, "label", None) or getattr(doc, "original_filename", None) or getattr(doc, "filename", None) or "資料"
        label = "アップロードされた資料"
        if doc.doc_type == "financial_statement":
            label = "アップロードされた決算書"
        elif doc.doc_type == "trial_balance":
            label = "アップロードされた試算表"
        elif doc.doc_type:
            label = f"アップロードされた{doc.doc_type}"
        if doc.period_label:
            sources.append(f"{label}（{doc.period_label}） {title}")
        else:
            sources.append(f"{label}: {title}")
    return sources


def _build_documents_context(documents: List[Document]) -> List[str]:
    snippets: List[str] = []
    for doc in documents:
        title = getattr(doc, "label", None) or getattr(doc, "original_filename", None) or getattr(doc, "filename", None) or "資料"
        meta_parts: List[str] = []
        if doc.doc_type:
            meta_parts.append(doc.doc_type)
        if doc.period_label:
            meta_parts.append(doc.period_label)
        meta = " / ".join(meta_parts)
        preview = (doc.content_text or "").strip()
        if preview:
            preview = preview[:120]
            snippets.append(f"{title}{f'（{meta}）' if meta else ''}: {preview}")
        else:
            snippets.append(f"{title}{f'（{meta}）' if meta else ''}")
    return snippets


def _build_conversation_text(messages: List[Message]) -> str:
    lines: List[str] = []
    for msg in messages[-40:]:
        role = "ユーザー" if msg.role == "user" else "yorizo"
        stamp = msg.created_at.isoformat() if msg.created_at else ""
        lines.append(f"{stamp} {role}: {msg.content}")
    return "\n".join(lines)


def build_conversation_report_data(db: Session, conversation_id: str) -> Optional[Dict[str, Any]]:
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        return None

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    doc_filters = [Document.conversation_id == conversation_id]
    if conversation.user_id:
        doc_filters.extend([Document.user_id == conversation.user_id, Document.company_id == conversation.user_id])
    documents = (
        db.query(Document)
        .filter(or_(*doc_filters))
        .order_by(Document.uploaded_at.desc())
        .limit(20)
        .all()
    )
    profile = None
    if conversation.user_id:
        profile = (
            db.query(CompanyProfile)
            .filter(CompanyProfile.user_id == conversation.user_id)
            .first()
        )

    meta = {
        "main_concern": conversation.main_concern,
        "period": _format_period(messages, conversation),
        "sources": _build_sources(profile, documents, messages),
    }

    conversation_text = _build_conversation_text(messages)
    docs_context = _build_documents_context(documents)

    homework_tasks = (
        db.query(HomeworkTask)
        .filter(HomeworkTask.conversation_id == conversation.id)
        .order_by(HomeworkTask.created_at.asc())
        .all()
    )
    pending_homework_count = sum(
        1 for task in homework_tasks if (task.status or HomeworkStatus.PENDING.value) != HomeworkStatus.DONE.value
    )

    finance_data = build_finance_section(
        profile=profile,
        documents=documents,
        conversation_count=db.query(Conversation).filter(Conversation.user_id == conversation.user_id).count()
        if conversation.user_id
        else len(messages),
        pending_homework_count=pending_homework_count,
    )

    concerns = generate_concerns(
        conversation_text=conversation_text,
        main_concern=conversation.main_concern,
        documents_summary=docs_context,
        history_messages=messages,
    )

    hints = generate_hints(
        main_concern=conversation.main_concern,
        concerns=concerns,
        finance_section=finance_data,
        documents_summary=docs_context,
        profile=profile,
    )

    return {
        "conversation": conversation,
        "messages": messages,
        "documents": documents,
        "profile": profile,
        "meta": meta,
        "conversation_text": conversation_text,
        "docs_context": docs_context,
        "homework_tasks": homework_tasks,
        "finance_data": finance_data,
        "pending_homework_count": pending_homework_count,
        "concerns": concerns,
        "hints": hints,
    }


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

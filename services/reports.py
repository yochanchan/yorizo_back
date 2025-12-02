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
from models import CompanyProfile, Conversation, Document, Message


def _clamp(value: Optional[float], default: int = 3, min_v: int = 1, max_v: int = 5) -> int:
    if value is None:
        return default
    return max(min_v, min(max_v, int(round(value))))


# -------------------- Finance scoring -------------------- #


def _score_profitability(documents: List[Document], profile: Optional[CompanyProfile]) -> Dict[str, object]:
    fin_docs = [d for d in documents if d.doc_type in {"financial_statement", "trial_balance"}]
    if not fin_docs and not (profile and profile.annual_sales_range):
        return {"score": None, "raw": None, "reason": "決算書が未登録のため収益性は未評価です。", "ned": True}
    raw = float(len(fin_docs)) if fin_docs else None
    score = 3 + (1 if len(fin_docs) >= 2 else 0)
    reason = "決算書や試算表から収益性を推定しました。" if fin_docs else "売上レンジの登録情報をもとに収益性を概算しました。"
    return {"score": score, "raw": raw, "reason": reason, "ned": False}


def _score_productivity(profile: Optional[CompanyProfile]) -> Dict[str, object]:
    if not profile or not profile.employees_range or not profile.annual_sales_range:
        return {"score": None, "raw": None, "reason": "従業員数や年商レンジの登録が不足しています。", "ned": True}
    return {
        "score": 3,
        "raw": None,
        "reason": f"従業員レンジ {profile.employees_range} と年商レンジ {profile.annual_sales_range} をもとに生産性を概算しました。",
        "ned": False,
    }


def _score_stability(documents: List[Document], pending_homework_count: int) -> Dict[str, object]:
    if not documents:
        return {"score": None, "raw": None, "reason": "財務資料が不足しているため安全性は未評価です。", "ned": True}
    score = 3
    if len(documents) >= 3:
        score += 1
    if pending_homework_count >= 3:
        score -= 1
    return {
        "score": _clamp(score),
        "raw": float(len(documents)),
        "reason": f"資料 {len(documents)} 件と未完了タスク {pending_homework_count} 件から安全性を評価しました。",
        "ned": False,
    }


def _score_growth(conversation_count: int) -> Dict[str, object]:
    if conversation_count <= 0:
        return {"score": None, "raw": None, "reason": "相談履歴が不足しているため成長性は未評価です。", "ned": True}
    score = 2 + min(3, conversation_count)
    return {
        "score": _clamp(score),
        "raw": float(conversation_count),
        "reason": f"これまでの相談回数 {conversation_count} 件をもとに成長性を評価しました。",
        "ned": False,
    }


def build_finance_section(
    profile: Optional[CompanyProfile],
    documents: List[Document],
    conversation_count: int,
    pending_homework_count: int,
) -> Dict[str, object]:
    metrics = {
        "profitability": _score_profitability(documents, profile),
        "productivity": _score_productivity(profile),
        "stability": _score_stability(documents, pending_homework_count),
        "growth": _score_growth(conversation_count),
    }

    label_map = {
        "profitability": "収益性",
        "productivity": "生産性",
        "stability": "安全性",
        "growth": "成長性",
    }

    scores: List[Dict[str, object]] = []
    missing: List[str] = []
    for key, label in label_map.items():
        m = metrics[key]
        if m["ned"]:
            missing.append(m["reason"])
        scores.append(
            {
                "key": key,
                "label": label,
                "raw": m["raw"],
                "industry_avg": None,
                "reason": m["reason"],
                "not_enough_data": m["ned"],
                "score": m["score"] if not m["ned"] else None,
            }
        )

    overview = "アップロードされた資料とプロフィールをもとに簡易評価を作成しました。"
    if missing:
        overview = "財務データが不足しています。決算書や試算表をアップロードすると、より具体的に評価できます。"

    return {"overview_comment": overview, "scores": scores}


# -------------------- Concerns and hints -------------------- #


def _build_concern_prompt(conversation_text: str, main_concern: Optional[str], documents_summary: List[str]) -> str:
    docs_text = "\n".join(documents_summary[:5]) if documents_summary else "なし"
    mc = main_concern or "未入力"
    return (
        "あなたは経営相談AI yorizo です。以下の会話とメインの関心事を読み、"
        "ユーザーが実際に話している内容だけから、最近の気になること（モヤモヤ）を3〜5個、日本語で短く列挙してください。"
        "会話に出ていない悩みを追加しないでください。JSONで {\"concerns\": [\"...\"]} の形で返してください。\n\n"
        f"[メインの関心事]\n{mc}\n\n"
        f"[会話抜粋]\n{conversation_text}\n\n"
        f"[資料メモ]\n{docs_text}\n"
    )


def generate_concerns(conversation_text: str, main_concern: Optional[str], documents_summary: List[str]) -> List[str]:
    if not conversation_text.strip():
        return []
    prompt = _build_concern_prompt(conversation_text, main_concern, documents_summary)
    raw = chat_completion_json(
        messages=[{"role": "system", "content": "JSONのみで出力してください。"}, {"role": "user", "content": prompt}],
        max_tokens=600,
    )
    data = json.loads(raw or "{}")
    concerns = data.get("concerns") or data.get("recent_concerns") or []
    return [str(c).strip() for c in concerns if str(c).strip()][:5]


def fallback_concerns(messages: List[Message]) -> List[str]:
    user_msgs = [m.content.strip() for m in messages if m.role == "user" and m.content.strip()]
    return user_msgs[-5:] if user_msgs else ["最近の気になることはまだ整理されていません。"]


def _build_hint_prompt(
    main_concern: Optional[str],
    concerns: List[str],
    finance_section: Optional[object],
    documents_summary: List[str],
    profile: Optional[CompanyProfile],
) -> str:
    finance_reasons: List[str] = []
    if finance_section and isinstance(finance_section, dict):
        for s in finance_section.get("scores", []):
            finance_reasons.append(f"{s.get('label')}: {s.get('reason')}")
    finance_text = "\n".join(finance_reasons) if finance_reasons else "財務評価はまだ十分ではありません。"
    docs_text = "\n".join(documents_summary[:5]) if documents_summary else "なし"
    profile_text = (
        f"業種: {profile.industry}, 従業員: {profile.employees_range}, 年商: {profile.annual_sales_range}"
        if profile
        else "プロフィール未登録"
    )
    concerns_text = "\n".join(concerns) if concerns else "未整理"
    main = main_concern or "未入力"
    return (
        "あなたは経営相談AI yorizo です。以下の情報のみを使って、今すぐ実行できる小さな一歩を2〜5個、日本語で提案してください。"
        "補助金など固有名はぼかし、データが足りない場合は「現時点の情報だけでは判断が難しいため、次回の相談時に確認が必要です」と書いてください。"
        "JSONで {\"hints\": [\"...\"]} の形で返してください。\n\n"
        f"[メインの関心事]\n{main}\n\n"
        f"[最近の気になること]\n{concerns_text}\n\n"
        f"[財務コメント]\n{finance_text}\n\n"
        f"[資料メモ]\n{docs_text}\n\n"
        f"[会社プロフィール]\n{profile_text}\n"
    )


def generate_hints(
    main_concern: Optional[str],
    concerns: List[str],
    finance_section: Optional[object],
    documents_summary: List[str],
    profile: Optional[CompanyProfile],
) -> List[str]:
    if not concerns:
        return []
    prompt = _build_hint_prompt(main_concern, concerns, finance_section, documents_summary, profile)
    raw = chat_completion_json(
        messages=[{"role": "system", "content": "JSONのみで出力してください。"}, {"role": "user", "content": prompt}],
        max_tokens=700,
    )
    data = json.loads(raw or "{}")
    hints = data.get("hints") or []
    return [str(h).strip() for h in hints if str(h).strip()][:5]


def fallback_hints() -> List[str]:
    return [
        "現時点の情報では判断が難しいため、次回の相談時に詳しい数字や資料を確認しましょう。",
        "決算書や試算表をアップロードすると、より具体的なアドバイスができます。",
    ]


# -------------------- Company analysis compatibility -------------------- #


def build_local_benchmark(
    profile: Optional[CompanyProfile],
    conversation_count: int,
    document_count: int,
    pending_homework_count: int,
) -> LocalBenchmark:
    metrics = {
        "profitability": _clamp(3 + (1 if document_count >= 2 else 0)),
        "productivity": _clamp(3),
        "stability": _clamp(3 - (1 if pending_homework_count >= 3 else 0)),
        "growth": _clamp(2 + min(3, conversation_count)),
        "organization": _clamp(4 - min(3, pending_homework_count)),
        "it_dx": _clamp(2 + min(3, document_count // 2)),
    }

    axes = [
        LocalBenchmarkAxis(id="profitability", label="収益性", score=metrics["profitability"] * 20, reason="簡易スコア"),
        LocalBenchmarkAxis(id="productivity", label="生産性", score=metrics["productivity"] * 20, reason="簡易スコア"),
        LocalBenchmarkAxis(id="stability", label="安全性", score=metrics["stability"] * 20, reason="簡易スコア"),
        LocalBenchmarkAxis(id="growth", label="成長性", score=metrics["growth"] * 20, reason="簡易スコア"),
        LocalBenchmarkAxis(id="organization", label="組織・人材", score=metrics["organization"] * 20, reason="簡易スコア"),
        LocalBenchmarkAxis(id="it_dx", label="IT・DX", score=metrics["it_dx"] * 20, reason="簡易スコア"),
    ]
    scores = [
        LocalBenchmarkScore(label="収益性", description="収益力の簡易スコアです。", score=metrics["profitability"], raw_value=None, reason="簡易スコア"),
        LocalBenchmarkScore(label="生産性", description="生産性の簡易スコアです。", score=metrics["productivity"], raw_value=None, reason="簡易スコア"),
        LocalBenchmarkScore(label="安全性", description="安全性の簡易スコアです。", score=metrics["stability"], raw_value=None, reason="簡易スコア"),
        LocalBenchmarkScore(label="成長性", description="成長性の簡易スコアです。", score=metrics["growth"], raw_value=None, reason="簡易スコア"),
    ]
    # LocalBenchmark schema may not have scores; include when available.
    try:
        return LocalBenchmark(axes=axes, scores=scores)  # type: ignore[arg-type]
    except TypeError:
        return LocalBenchmark(axes=axes)


# -------------------- Company analysis report (legacy endpoint) -------------------- #


def _strip_choice_prefix(value: Optional[str]) -> str:
    if not value:
        return ""
    if value.startswith("[choice_id:"):
        closing = value.find("]")
        if closing != -1:
            return value[closing + 1 :].strip()
    return value.strip()


def _categorize_pain_points(conversations: List[Conversation]) -> List[CompanyAnalysisCategory]:
    categories = {
        "売上・顧客": [],
        "コスト・原価": [],
        "資金繰り": [],
        "人手・採用": [],
        "業務・仕組み": [],
        "その他": [],
    }
    keywords = {
        "売上・顧客": ["売上", "顧客", "集客", "客数", "単価"],
        "コスト・原価": ["原価", "コスト", "仕入", "経費", "在庫"],
        "資金繰り": ["資金", "キャッシュ", "支払い", "入金"],
        "人手・採用": ["採用", "人材", "人手", "教育"],
        "業務・仕組み": ["業務", "仕組み", "効率", "生産性"],
    }
    for conv in conversations:
        text = _strip_choice_prefix(conv.title or conv.main_concern or "")
        if not text:
            continue
        placed = False
        for cat, kw in keywords.items():
            if any(k in text for k in kw):
                categories[cat].append(text)
                placed = True
                break
        if not placed:
            categories["その他"].append(text)
    result: List[CompanyAnalysisCategory] = []
    for cat, items in categories.items():
        if items:
            result.append(CompanyAnalysisCategory(category=cat, items=items[:3]))
    if not result:
        result.append(CompanyAnalysisCategory(category="その他", items=["課題はまだ整理されていません。"]))
    return result


def build_company_analysis_report(db: Session, company_id: str) -> CompanyAnalysisReport:
    profile = (
        db.query(CompanyProfile)
        .filter((CompanyProfile.user_id == company_id) | (CompanyProfile.id == company_id))
        .first()
    )
    if not profile:
        raise ValueError("Company profile not found")

    conversations = (
        db.query(Conversation)
        .filter(Conversation.user_id == profile.user_id)
        .order_by(Conversation.started_at.desc())
        .limit(10)
        .all()
    )
    latest_conversation = conversations[0] if conversations else None

    documents = (
        db.query(Document)
        .filter((Document.user_id == profile.user_id) | (Document.company_id == profile.user_id))
        .order_by(Document.uploaded_at.desc())
        .limit(10)
        .all()
    )

    pain_points = _categorize_pain_points(conversations)

    finance_data = build_finance_section(
        profile=profile,
        documents=documents,
        conversation_count=len(conversations),
        pending_homework_count=0,
    )
    finance_scores: List[LocalBenchmarkScore] = [
        LocalBenchmarkScore(
            label=s["label"],
            description=f"{s['label']}の簡易スコアです。",
            score=s.get("score"),
            raw_value=s.get("raw"),
            industry_avg=s.get("industry_avg"),
            reason=s.get("reason"),
        )
        for s in finance_data.get("scores", [])
    ]

    local_benchmark = build_local_benchmark(profile, len(conversations), len(documents), 0)

    summary_parts: List[str] = []
    if profile.company_name:
        summary_parts.append(f"{profile.company_name}の状況です。")
    if profile.industry:
        summary_parts.append(f"業種は{profile.industry}。")
    if latest_conversation:
        topic = _strip_choice_prefix(latest_conversation.title or latest_conversation.main_concern or "")
        if topic:
            summary_parts.append(f"最近の相談テーマは「{topic}」。")
    summary = "".join(summary_parts) or "会社の状況を整理しています。"

    strengths = ["決算書や資料があれば、より具体的な分析が可能です。"]
    weaknesses = ["追加の財務資料と相談履歴があると、評価が精緻化されます。"]
    action_items = [
        "決算書や試算表をアップロードして、財務評価を充実させましょう。",
        "次回の相談で具体的に解決したいテーマを1つ決めておきましょう。",
    ]

    return CompanyAnalysisReport(
        company_id=profile.user_id,
        last_updated_at=datetime.utcnow(),
        summary=summary,
        basic_info_note=f"所在地: {profile.location_prefecture or '未登録'} / 従業員: {profile.employees_range or '未登録'} / 年商: {profile.annual_sales_range or '未登録'}",
        finance_scores=finance_scores,
        pain_points=pain_points,
        strengths=strengths,
        weaknesses=weaknesses,
        action_items=action_items,
        local_benchmark=local_benchmark,
    )

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List

from sqlalchemy.orm import Session

from app.schemas.reports import CompanyAnalysisCategory, CompanyAnalysisReport, LocalBenchmarkScore
from models import CompanyProfile, Conversation, Document, HomeworkTask

CHOICE_ID_PATTERN = re.compile(r"^\[choice_id:[^\]]+\]\s*")

CATEGORY_RULES: Dict[str, List[str]] = {
    "売上・顧客": ["売上", "顧客", "集客", "販路", "マーケ"],
    "コスト・利益": ["費用", "コスト", "利益", "原価", "粗利"],
    "資金繰り・借入": ["資金", "キャッシュ", "借入", "返済", "融資"],
    "人手・採用・組織": ["人手", "採用", "人材", "組織", "教育"],
    "業務のバタバタ・生産性": ["業務", "フロー", "生産性", "残業", "効率"],
}


def _strip_choice_prefix(value: str | None) -> str:
    if not value:
        return ""
    return CHOICE_ID_PATTERN.sub("", value).strip()


def _estimate_score_from_range(value: str | None) -> int | None:
    if not value:
        return None
    if "億" in value:
        if any(token in value for token in ["10", "20"]):
            return 5
        return 4
    digits = [int(num) for num in re.findall(r"\d+", value)]
    if not digits:
        return None
    max_digit = max(digits)
    if max_digit >= 5000:
        return 4
    if max_digit >= 1000:
        return 3
    if max_digit >= 500:
        return 2
    return 1


def _score_from_conversation_count(count: int) -> int:
    if count >= 5:
        return 4
    if count >= 3:
        return 3
    if count >= 1:
        return 2
    return 1


def _build_summary(profile: CompanyProfile | None, latest_conversation: Conversation | None) -> str:
    parts: List[str] = []
    if profile and profile.company_name:
        parts.append(f"{profile.company_name}の近況です")
    else:
        parts.append("最新の相談内容をまとめています")
    if profile and profile.industry:
        parts.append(f"主な業種は{profile.industry}です")
    if profile and profile.employees_range:
        parts.append(f"従業員規模は{profile.employees_range}")
    if profile and profile.annual_sales_range:
        parts.append(f"年商レンジは{profile.annual_sales_range}")
    if latest_conversation:
        topic = _strip_choice_prefix(latest_conversation.title or latest_conversation.main_concern or "")
        if topic:
            parts.append(f"直近の相談テーマは「{topic}」です")
    return "、".join(parts) + "。"


def _build_overview(profile: CompanyProfile | None) -> str:
    if not profile:
        return "会社情報はまだ登録されていません。"
    segments: List[str] = []
    if profile.location_prefecture:
        segments.append(f"{profile.location_prefecture}を拠点としています")
    if profile.years_in_business:
        segments.append(f"創業から{profile.years_in_business}年目です")
    if profile.industry:
        segments.append(f"主業種は{profile.industry}")
    if profile.employees_range:
        segments.append(f"従業員レンジ: {profile.employees_range}")
    if profile.annual_sales_range:
        segments.append(f"年商レンジ: {profile.annual_sales_range}")
    return " / ".join(segments) if segments else "基本情報は登録済みですが、詳細は未入力です。"


def _categorize_pain_points(conversations: List[Conversation]) -> List[CompanyAnalysisCategory]:
    bucket: Dict[str, List[str]] = {category: [] for category in CATEGORY_RULES}
    bucket["その他"] = []

    for conv in conversations:
        text = _strip_choice_prefix(conv.title or conv.main_concern or "")
        if not text:
            continue
        matched = False
        for category, keywords in CATEGORY_RULES.items():
            if any(keyword in text for keyword in keywords):
                bucket[category].append(text)
                matched = True
                break
        if not matched:
            bucket["その他"].append(text)

    categories: List[CompanyAnalysisCategory] = []
    for category, items in bucket.items():
        if items:
            categories.append(CompanyAnalysisCategory(category=category, items=items[:3]))
    if not categories:
        categories.append(CompanyAnalysisCategory(category="その他", items=["課題の整理はこれから進めていきましょう。"]))
    return categories


def _build_finance_scores(
    profile: CompanyProfile | None,
    conversation_count: int,
    document_count: int,
    pending_homework_count: int,
) -> List[LocalBenchmarkScore]:
    sales_score = _estimate_score_from_range(profile.annual_sales_range if profile else None)
    employees_score = _estimate_score_from_range(profile.employees_range if profile else None)
    productivity = None
    if sales_score is not None and employees_score is not None:
        productivity = max(1, min(5, sales_score - max(employees_score - 2, 0)))

    safety_score = 3 + (1 if document_count >= 3 else 0) - (1 if pending_homework_count >= 3 else 0)
    growth_score = _score_from_conversation_count(conversation_count)

    return [
        LocalBenchmarkScore(
            label="収益性",
            description="売上高や利益の傾向を概観します。",
            score=sales_score,
        ),
        LocalBenchmarkScore(
            label="生産性",
            description="一人あたり付加価値や業務効率の兆しです。",
            score=productivity,
        ),
        LocalBenchmarkScore(
            label="安全性",
            description="自己資本や資金繰りの安定度を示します。",
            score=max(1, min(5, safety_score)),
        ),
        LocalBenchmarkScore(
            label="成長性",
            description="売上・利益の伸びしろとチャレンジ状況です。",
            score=growth_score,
        ),
    ]


def _build_strengths(profile: CompanyProfile | None, documents: List[Document], categories: List[CompanyAnalysisCategory]) -> List[str]:
    strengths: List[str] = []
    if profile and profile.industry:
        strengths.append(f"{profile.industry}での経験と知見があります。")
    if documents:
        strengths.append("決算書や資料が整理されており、外部相談に活用しやすい状態です。")
    top_category = categories[0] if categories else None
    if top_category and top_category.category != "その他":
        strengths.append(f"{top_category.category}の課題を言語化できていることは強みです。")
    if not strengths:
        strengths.append("日々の気付きが蓄積されており、いつでも改善に動ける素地があります。")
    return strengths[:5]


def _build_weaknesses(categories: List[CompanyAnalysisCategory]) -> List[str]:
    weaknesses: List[str] = []
    for category in categories[:3]:
        weaknesses.append(f"{category.category}に課題感があります: {category.items[0]}")
    if not weaknesses:
        weaknesses.append("大きな弱みは明確になっていません。これから整理していきましょう。")
    return weaknesses[:5]


def _build_action_items(pending_homework: List[HomeworkTask]) -> List[str]:
    action_items = [task.title for task in pending_homework if task.title][:5]
    if len(action_items) < 3:
        action_items.extend(
            [
                "早期に取り組める改善を1つ決め、1週間以内に着手する。",
                "金融機関やよろず支援拠点に共有したい数字・資料を整える。",
                "専門家に相談したいテーマを3つメモにまとめる。",
            ],
        )
    return action_items[:5]


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

    pending_homework = (
        db.query(HomeworkTask)
        .filter(HomeworkTask.user_id == profile.user_id, HomeworkTask.status == "pending")
        .order_by(HomeworkTask.created_at.desc())
        .limit(10)
        .all()
    )

    pain_points = _categorize_pain_points(conversations)
    finance_scores = _build_finance_scores(
        profile,
        conversation_count=len(conversations),
        document_count=len(documents),
        pending_homework_count=len(pending_homework),
    )
    strengths = _build_strengths(profile, documents, pain_points)
    weaknesses = _build_weaknesses(pain_points)
    action_items = _build_action_items(pending_homework)

    return CompanyAnalysisReport(
        company_id=profile.user_id,
        last_updated_at=datetime.utcnow(),
        summary=_build_summary(profile, latest_conversation),
        basic_info_note=_build_overview(profile),
        finance_scores=finance_scores,
        pain_points=pain_points,
        strengths=strengths,
        weaknesses=weaknesses,
        action_items=action_items,
    )

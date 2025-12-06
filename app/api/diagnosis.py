import uuid
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


class CompanyProfile(BaseModel):
    industry: str = Field(..., description="業種")
    employees: str = Field(..., description="従業員レンジ")
    annual_sales_range: str = Field(..., description="年間売上レンジ")
    years_in_business: Optional[int] = Field(None, description="創業年数")


class DiagnosisRequest(BaseModel):
    company_profile: CompanyProfile
    main_concern: str
    detail: Optional[str] = None
    user_id: Optional[str] = None


class Score(BaseModel):
    sales: str
    profit: str
    cashflow: str
    hr: str
    dx: str


class DiagnosisResponse(BaseModel):
    summary: str
    score: Score
    homework: List[str]
    suggest_next_step: str
    diagnosis_id: str


router = APIRouter()


def _insight(concern: str) -> tuple[str, List[str], Score]:
    base_score = Score(sales="△", profit="△", cashflow="◯", hr="◯", dx="△")
    if "売上" in concern:
        return (
            "売上が頭打ちになっているようです。客数・単価・回数のどこで伸びが止まっているか棚卸しすると次の一手が見えます。",
            ["直近3か月の客数・単価・回数を分けて見る", "粗利率の高い商品を3つ洗い出す", "既存客向けの再来店施策を1つ決める"],
            base_score,
        )
    if "資金" in concern:
        score = Score(sales="◯", profit="△", cashflow="△", hr="◯", dx="△")
        return (
            "資金繰りの不安があるようです。入出金カレンダーを作り、固定費を先に確保するのが安全です。",
            ["今月と来月の入出金をカレンダーに記入", "支払サイト・回収サイトを整理", "固定費・変動費で削減できる項目を探す"],
            score,
        )
    if "人手" in concern or "採用" in concern:
        score = Score(sales="◯", profit="◯", cashflow="◯", hr="△", dx="△")
        return (
            "現場のリソース不足が課題のようです。業務棚卸しと優先度付けを行い、外注やパート活用も検討しましょう。",
            ["業務を30分単位で棚卸しする", "優先度×頻度で仕分ける", "外注・パートに任せられる作業を3つ列挙"],
            score,
        )
    if "IT" in concern or "DX" in concern:
        score = Score(sales="◯", profit="◯", cashflow="◯", hr="◯", dx="△")
        return (
            "デジタル化の遅れが気になっているようです。目的を1つに絞り、無料ツールから小さく試すのがおすすめです。",
            ["困っている業務を1つ選ぶ", "既存ツールの活用度を振り返る", "無料/低コストのSaaSを1つ試す", "手順を簡単にマニュアル化する"],
            score,
        )
    return (
        "悩みを整理すれば次のアクションを決めやすくなります。数字・事実・感じていることを分けて書いてみましょう。",
        ["悩みを一文で書く", "背景となる事実を3つ挙げる", "理想の状態を1つ書き出す", "明日できる小さな一歩を決める"],
        base_score,
    )


@router.post("/diagnosis", response_model=DiagnosisResponse)
async def create_diagnosis(payload: DiagnosisRequest) -> DiagnosisResponse:
    summary, homework, score = _insight(payload.main_concern)
    suggest_next_step = "上記の宿題をまとめて、よろず支援拠点で相談してみましょう。"
    diagnosis_id = str(uuid.uuid4())
    return DiagnosisResponse(
        summary=summary,
        score=score,
        homework=homework,
        suggest_next_step=suggest_next_step,
        diagnosis_id=diagnosis_id,
    )

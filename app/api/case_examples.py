from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.case_example import CaseExample, CaseExampleResponse

router = APIRouter()


def _base_cases() -> list[CaseExample]:
    return [
        CaseExample(
            title="オンライン初回面談の歩留まり改善",
            industry="BtoBサービス",
            result="リスケ自動化で完了率を70%->88%に改善",
            actions=[
                "確定後に候補日リンクを同封して即リスケ許可",
                "5分前リマインダーと接続テスト手順を送付",
                "録画とサマリを自動送信し次回の宿題を明確化",
            ],
        ),
        CaseExample(
            title="相談後のナーチャリングテンプレート",
            industry="士業",
            result="提案化率+15%",
            actions=[
                "初回ヒアリングで課題タグを3つ付与",
                "タグ別の成功事例記事を自動送信",
                "1週間後に課題別の再診リンクを送付",
            ],
        ),
    ]


def _in_person_cases() -> list[CaseExample]:
    return [
        CaseExample(
            title="来店導線を整理して来訪率120%",
            industry="小売・来店型",
            result="予約後のSMSフォローで当日来店率が3割改善",
            actions=[
                "前日SMSで道順と駐車場案内を送付",
                "当日朝に担当者の顔写真と一言を送付",
                "キャンセル防止のリマインダー自動化",
            ],
        ),
        CaseExample(
            title="初回体験の同意書をペーパーレス化",
            industry="美容",
            result="受付時間を-10分/件、回転率向上",
            actions=[
                "Web同意書を予約完了メールに添付",
                "来店前に施術希望をチェックさせヒアリング短縮",
                "署名データをカルテに自動格納",
            ],
        ),
    ]


@router.get("/case-examples", response_model=CaseExampleResponse)
async def list_case_examples(
    channel: Optional[str] = Query(None, description="online or in-person"),
    industry: Optional[str] = Query(None, description="Industry hint"),
) -> CaseExampleResponse:
    if channel == "in-person":
        cases = _in_person_cases()
    else:
        cases = _base_cases()

    if industry:
        matched = [case for case in cases if industry in case.industry]
        if matched:
            cases = matched

    return CaseExampleResponse(cases=cases)

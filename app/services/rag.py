from typing import List, Optional

from sqlalchemy.orm import Session

from app.rag.store import fetch_recent_documents, query_similar


def _resolve_owner_id(user_id: Optional[str], company_id: Optional[str]) -> Optional[str]:
    """ユーザーID / 会社ID から RAG ストア用の owner_id を決める。"""
    return user_id or company_id


async def retrieve_context(
    *,
    db: Session,  # いまは未使用だがインターフェース揃えのため残す
    user_id: Optional[str],
    company_id: Optional[str],
    query: str,
    top_k: int = 8,
) -> List[str]:
    """
    チャットで使う RAG コンテキストを取得するヘルパー。

    - user_id / company_id から owner_id を決定
    - query があれば query_similar を優先して呼び出し
    - query が空の場合は fetch_recent_documents で直近文書を使う
    - 戻り値は重複を除いたテキストスニペットのリスト
    """
    # いまのところ SQLAlchemy Session は不要なので未使用
    del db  # noqa: ARG001

    owner_id = _resolve_owner_id(user_id, company_id)

    try:
        if query:
            docs = await query_similar(
                query,
                k=top_k,
                user_id=owner_id,
                company_id=company_id,
            )
        else:
            docs = await fetch_recent_documents(
                limit=top_k,
                user_id=owner_id,
                company_id=company_id,
            )
    except Exception:
        # RAG 側のエラーでチャット全体が死なないように、ここでは空リストで返す
        return []

    texts: List[str] = []
    for d in docs:
        text = d.get("text")
        if text and text not in texts:
            texts.append(text)
    return texts

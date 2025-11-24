from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from app.schemas.rag import RagChatRequest, RagChatResponse
from app.core.openai_client import generate_chat_reply
from app.rag.store import query_similar, _ensure_rag_enabled
from database import get_db

router = APIRouter()


@router.post("/rag/chat", response_model=RagChatResponse)
async def rag_chat_endpoint(payload: RagChatRequest, db: Session = Depends(get_db)) -> RagChatResponse:
    """
    RAG-style chat for Japanese small business owners.
    """
    try:
        _ensure_rag_enabled()
        docs = await query_similar(db, user_id=None, query=payload.question, top_k=5)
        context_texts = [d["content"] for d in docs]

        system_content = (
            "あなたは日本の小規模事業者向けの経営相談AI『Yorizo』です。"
            "以下の「参考情報」を踏まえつつ、質問に日本語で答えてください。"
            "参考情報に書かれていないことを推測で断定しないでください。"
            "売上・利益・資金繰り・人手不足・IT・DX・税務などの観点から、"
            "3〜5個の具体的な視点や次の一歩を提案してください。"
        )

        context_block = "\n\n".join(
            [f"【参考情報{i+1}】\n{txt}" for i, txt in enumerate(context_texts)]
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "system", "content": f"参考情報:\n{context_block}"},
        ]

        for h in payload.history:
            messages.append({"role": "user", "content": h})

        messages.append({"role": "user", "content": payload.question})

        answer = await generate_chat_reply(messages, with_system_prompt=False)

        return RagChatResponse(answer=answer, contexts=context_texts)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

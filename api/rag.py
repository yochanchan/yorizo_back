from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.openai_client import generate_chat_reply
from app.rag.store import index_documents, query_similar
from app.schemas.rag import (
    RagChatRequest,
    RagChatResponse,
    RagDocumentCreateRequest,
    RagDocumentCreateResponse,
    RagDocumentResponse,
    RagQueryRequest,
    RagQueryResponse,
    RagSimilarDocument,
)
from database import get_db
from models import RAGDocument

router = APIRouter()


def _resolve_owner_id(user_id: str | None, company_id: str | None) -> str | None:
    return user_id or company_id


@router.post(
    "/rag/documents",
    response_model=RagDocumentCreateResponse,
    summary="Register RAG documents",
    description="受け取ったドキュメント群をベクトル化し、rag_documents に保存します。",
)
async def create_rag_documents(payload: RagDocumentCreateRequest) -> RagDocumentCreateResponse:
    owner_id = _resolve_owner_id(payload.user_id, payload.company_id)
    if not payload.documents:
        raise HTTPException(status_code=400, detail="documents is required")

    try:
        items = []
        for doc in payload.documents:
            data = doc.model_dump()
            data["user_id"] = doc.user_id or doc.company_id or owner_id
            data["text"] = doc.text  # ensure key exists for store
            items.append(data)

        saved_docs = await index_documents(items, default_user_id=owner_id)
        return RagDocumentCreateResponse(documents=[RagDocumentResponse.model_validate(d) for d in saved_docs])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/rag/documents",
    response_model=list[RagDocumentResponse],
    summary="List RAG documents",
    description="rag_documents を時系列で取得します。user_id/company_id を指定すると絞り込みます。",
)
async def list_rag_documents(
    user_id: str | None = None,
    company_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[RagDocumentResponse]:
    owner_id = _resolve_owner_id(user_id, company_id)
    query = db.query(RAGDocument).order_by(RAGDocument.created_at.desc())
    if owner_id:
        query = query.filter(RAGDocument.user_id == owner_id)
    docs = query.limit(limit).all()
    return [RagDocumentResponse.model_validate(doc) for doc in docs]


@router.post(
    "/rag/search",
    response_model=RagQueryResponse,
    summary="Search similar RAG documents",
    description="クエリを埋め込み、rag_documents から類似度上位を返します。",
)
async def rag_search(payload: RagQueryRequest) -> RagQueryResponse:
    try:
        owner_id = _resolve_owner_id(payload.user_id, payload.company_id)
        results = await query_similar(payload.query, k=payload.top_k, user_id=owner_id)
        matches = [
            RagSimilarDocument(
                id=doc["id"],
                title=doc["title"],
                text=doc["text"],
                metadata=doc.get("metadata") or {},
                score=float(doc.get("score", 0.0)),
            )
            for doc in results
        ]
        return RagQueryResponse(matches=matches)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/rag/chat",
    response_model=RagChatResponse,
    summary="RAG chat with retrieved context",
    description="クエリを基に類似ドキュメントを検索し、コンテキストを付与して回答します。",
)
async def rag_chat_endpoint(payload: RagChatRequest) -> RagChatResponse:
    try:
        owner_id = _resolve_owner_id(payload.user_id, payload.company_id)

        query_text: str | None = payload.question
        if payload.messages:
            for msg in reversed(payload.messages):
                if msg.role == "user" and msg.content:
                    query_text = msg.content
                    break
        if not query_text and payload.history:
            query_text = payload.history[-1]
        if not query_text:
            raise HTTPException(status_code=400, detail="No user query provided")

        docs = await query_similar(query_text, k=payload.top_k, user_id=owner_id)
        context_texts = [d["text"] for d in docs]
        citations = [int(d["id"]) for d in docs if d.get("id") is not None]

        system_content = (
            "あなたは日本の小規模事業者を支援する経営相談AI『Yorizo』です。"
            "以下の「参考情報」を踏まえつつ、質問に日本語で答えてください。"
            "参考情報に書かれていないことを推測で断定せず、"
            "売上・利益・資金繰り・人手不足・IT・DX・税務などの観点から、"
            "3〜5個の具体的な視点や次の一歩を提案してください。"
        )

        context_block = "\n\n".join([f"【参考情報{i+1}】\n{txt}" for i, txt in enumerate(context_texts)])

        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "system",
                "content": f"参考情報:\n{context_block}" if context_block else "参考情報はありません。",
            },
        ]

        for history_item in payload.history:
            messages.append({"role": "user", "content": history_item})
        for msg in payload.messages:
            messages.append({"role": msg.role, "content": msg.content})

        if not any(m.get("role") == "user" for m in messages):
            messages.append({"role": "user", "content": query_text})

        answer = await generate_chat_reply(messages, with_system_prompt=False)

        return RagChatResponse(answer=answer, contexts=context_texts, citations=citations)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

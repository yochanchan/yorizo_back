import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.openai_client import chat_text_safe
from app.rag.store import (
    EmbeddingUnavailableError,
    fetch_recent_documents,
    index_documents,
    query_similar,
)
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
from app.models import RAGDocument

router = APIRouter()
logger = logging.getLogger(__name__)
FALLBACK_RAG_MESSAGE = "AI 連携が利用できません。資料をご確認のうえ、専門家にご相談ください。"


def _resolve_owner_id(user_id: str | None, company_id: str | None) -> str | None:
    return user_id or company_id


@router.post(
    "/rag/documents",
    response_model=RagDocumentCreateResponse,
    summary="Register RAG documents",
    description="Embed received documents and store them for retrieval.",
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
    except EmbeddingUnavailableError as exc:
        logger.error("%s (%s)", FALLBACK_RAG_MESSAGE, exc)
        raise HTTPException(status_code=503, detail=FALLBACK_RAG_MESSAGE) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/rag/documents",
    response_model=list[RagDocumentResponse],
    summary="List RAG documents",
    description="Fetch stored RAG documents, optionally filtered by user/company.",
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
    description="Embed a query and return top-matching documents.",
)
async def rag_search(payload: RagQueryRequest) -> RagQueryResponse:
    try:
        owner_id = _resolve_owner_id(payload.user_id, payload.company_id)
        results = await query_similar(
            payload.query,
            k=payload.top_k,
            user_id=owner_id,
            company_id=payload.company_id,
            source_types=payload.source_types,
        )
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
    except EmbeddingUnavailableError as exc:
        logger.error("%s (%s)", FALLBACK_RAG_MESSAGE, exc)
        raise HTTPException(status_code=503, detail=FALLBACK_RAG_MESSAGE) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post(
    "/rag/chat",
    response_model=RagChatResponse,
    summary="RAG chat with retrieved context",
    description="Search relevant documents and answer with references.",
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

        docs = await query_similar(
            query_text,
            k=payload.top_k,
            user_id=owner_id,
            company_id=payload.company_id,
        )
        context_texts = [d["text"] for d in docs]
        citations = [int(d["id"]) for d in docs if d.get("id") is not None]

        system_content = (
            "You are Yorizo, a business consultation assistant for Japanese small businesses. "
            "Answer in Japanese using the reference information when available. "
            "If the references do not include the answer, avoid guessing. "
            "Offer around three concrete perspectives or next steps (sales, profit, cash flow, staffing, IT/DX, tax, etc.)."
        )

        context_block = "\n\n".join([f"[Reference {i+1}]\n{txt}" for i, txt in enumerate(context_texts)])

        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "system",
                "content": f"References:\n{context_block}" if context_block else "No references provided.",
            },
        ]

        for history_item in payload.history:
            messages.append({"role": "user", "content": history_item})
        for msg in payload.messages:
            messages.append({"role": msg.role, "content": msg.content})

        if not any(m.get("role") == "user" for m in messages):
            messages.append({"role": "user", "content": query_text})

        llm_result = await chat_text_safe("LLM-RAG-01-v1", messages)
        if not llm_result.ok or not llm_result.value:
            logger.warning("rag chat fallback: %s", llm_result.error)
            return RagChatResponse(answer=FALLBACK_RAG_MESSAGE, contexts=[], citations=[])

        return RagChatResponse(answer=llm_result.value, contexts=context_texts, citations=citations)
    except EmbeddingUnavailableError as exc:
        logger.error("%s (%s)", FALLBACK_RAG_MESSAGE, exc)
        return RagChatResponse(answer=FALLBACK_RAG_MESSAGE, contexts=[], citations=[])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

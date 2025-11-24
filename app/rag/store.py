from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.config import settings
from app.core.openai_client import embed_texts
from models import RAGDocument


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _ensure_rag_enabled() -> None:
    if not settings.rag_enabled:
        raise HTTPException(
            status_code=503,
            detail="RAG is disabled in this environment.",
        )


async def index_document(
    db: Session,
    *,
    user_id: Optional[str],
    source: str,
    title: Optional[str],
    content: str,
) -> RAGDocument:
    _ensure_rag_enabled()
    embedding_vec = (await embed_texts([content]))[0]
    doc = RAGDocument(
        user_id=user_id,
        source=source,
        title=title,
        content=content,
        embedding=json.dumps(embedding_vec),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


async def query_similar(
    db: Session,
    *,
    user_id: Optional[str],
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    _ensure_rag_enabled()
    query_vec = (await embed_texts([query]))[0]

    stmt = select(RAGDocument)
    if user_id:
        stmt = stmt.where(RAGDocument.user_id == user_id)
    result = db.execute(stmt)
    docs = result.scalars().all()

    scored: List[tuple[float, RAGDocument]] = []
    for d in docs:
        if not d.embedding:
            continue
        try:
            emb = json.loads(d.embedding)
            score = cosine_similarity(query_vec, emb)
            scored.append((score, d))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)

    top_docs: List[Dict[str, Any]] = []
    for score, d in scored[:top_k]:
        top_docs.append(
            {
                "id": d.id,
                "title": d.title,
                "content": d.content,
                "source": d.source,
                "score": score,
            }
        )
    return top_docs

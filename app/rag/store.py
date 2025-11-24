from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from database import SessionLocal
from models import RAGDocument
from app.core.openai_client import embed_texts


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity; if lengths differ, truncate to the shorter."""
    if not a or not b:
        return 0.0

    if len(a) != len(b):
        n = min(len(a), len(b))
        a = a[:n]
        b = b[:n]

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))

    if na == 0.0 or nb == 0.0:
        return 0.0

    return dot / (na * nb)


async def query_similar(question: str, k: int = 5, user_id: Optional[str] = None, company_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Simple MySQL-backed RAG retrieval:
    - embed the question
    - fetch all matching RAGDocument rows
    - compute cosine similarity against stored embeddings
    - return top-k docs with at least a 'text' field
    """
    query_emb_list = await embed_texts(question)
    if not query_emb_list:
        return []
    query_emb = query_emb_list[0]
    owner_id = user_id or company_id

    session: Session = SessionLocal()
    try:
        query = session.query(RAGDocument)
        if owner_id:
            query = query.filter(RAGDocument.user_id == owner_id)
        docs: List[RAGDocument] = query.all()
    finally:
        session.close()

    scored: List[tuple[float, RAGDocument]] = []
    for doc in docs:
        emb = doc.embedding
        if not emb:
            continue

        if isinstance(emb, dict) and "embedding" in emb:
            emb = emb["embedding"]

        if not isinstance(emb, (list, tuple)):
            continue

        score = _cosine_similarity(query_emb, emb)
        scored.append((score, doc))

    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)

    results: List[Dict[str, Any]] = []
    top_n = k or 5
    for score, doc in scored[:top_n]:
        results.append(
            {
                "id": doc.id,
                "title": doc.title,
                "text": doc.content,
                "metadata": doc.metadata_json or {},
                "score": float(score),
            }
        )

    return results


async def index_documents(documents: List[Dict[str, Any]], default_user_id: Optional[str] = None) -> List[RAGDocument]:
    """
    Bulk upsert helper for RAG documents.
    Each item:
    {
      "id": Optional[int],  # for updates
      "title": str,
      "text": str,
      "metadata": dict,
      "user_id": Optional[str],
      "source_type": Optional[str],
      "source_id": Optional[str],
    }

    Returns the saved RAGDocument objects (with IDs populated).
    """
    if not documents:
        return []

    texts = [d["text"] for d in documents]
    embeddings = await embed_texts(texts)

    session: Session = SessionLocal()
    saved_docs: List[RAGDocument] = []
    try:
        for payload, emb in zip(documents, embeddings):
            raw_id = payload.get("id")
            try:
                doc_id = int(raw_id) if raw_id is not None else None
            except (TypeError, ValueError):
                doc_id = None

            doc = session.get(RAGDocument, doc_id) if doc_id is not None else None
            owner_id = payload.get("user_id") or default_user_id

            # Upsert by source_id + user_id if provided
            if doc is None and payload.get("source_id") and owner_id:
                doc = (
                    session.query(RAGDocument)
                    .filter(RAGDocument.source_id == payload["source_id"], RAGDocument.user_id == owner_id)
                    .first()
                )
            if doc is None:
                doc = RAGDocument()
                session.add(doc)

            text_value = payload.get("text")
            if text_value is None:
                raise ValueError("Document payload missing 'text'")

            doc.user_id = owner_id
            doc.title = payload.get("title") or (text_value or "")[:80] or ""
            doc.source_type = payload.get("source_type") or (doc.source_type or "manual")
            doc.source_id = payload.get("source_id")
            doc.content = text_value
            doc.metadata_json = payload.get("metadata") or {}
            doc.embedding = emb
            saved_docs.append(doc)

        session.commit()
        for doc in saved_docs:
            session.refresh(doc)
        return saved_docs
    finally:
        session.close()

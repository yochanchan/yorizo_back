from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

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


async def query_similar(question: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Simple MySQL-backed RAG retrieval:
    - embed the question
    - fetch all RAGDocument rows
    - compute cosine similarity against stored embeddings
    - return top-k docs with at least a 'text' field
    """
    query_emb_list = await embed_texts(question)
    if not query_emb_list:
        return []
    query_emb = query_emb_list[0]

    session: Session = SessionLocal()
    try:
        docs: List[RAGDocument] = session.query(RAGDocument).all()
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
                "metadata": doc.metadata or {},
                "score": float(score),
            }
        )

    return results


async def index_documents(documents: List[Dict[str, Any]]) -> None:
    """
    Bulk upsert helper for RAG documents.
    Each item:
    {
      "id": Optional[int],  # for updates
      "title": str,
      "text": str,
      "metadata": dict
    }
    """
    if not documents:
        return

    texts = [d["text"] for d in documents]
    embeddings = await embed_texts(texts)

    session: Session = SessionLocal()
    try:
        for payload, emb in zip(documents, embeddings):
            doc_id = payload.get("id")
            doc = session.get(RAGDocument, doc_id) if doc_id is not None else None
            if doc is None:
                doc = RAGDocument()
                session.add(doc)

            doc.title = payload.get("title") or ""
            doc.content = payload["text"]
            doc.metadata = payload.get("metadata") or {}
            doc.embedding = emb

        session.commit()
    finally:
        session.close()

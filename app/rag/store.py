from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from database import SessionLocal
from app.models import RAGDocument
from app.core.config import settings
from app.core.openai_client import embed_texts

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    """Raised when embeddings cannot be generated (e.g., missing API key)."""


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


def get_store(collection_name: str) -> Dict[str, Any]:
    """
    Placeholder for collection-scoped store access.
    Current implementation is DB-backed; collection_name is carried in metadata.
    """
    return {"name": collection_name, "persist_dir": getattr(settings, "rag_persist_dir", None)}


async def add_documents(collection_name: str, texts: List[str], metadatas: List[Dict[str, Any]]) -> List[RAGDocument]:
    """
    Embed and store documents for a given collection.
    Each metadata dict can include user_id, company_id, source_id, etc.
    """
    if not texts:
        return []

    try:
        embeddings = await embed_texts(texts)
    except RuntimeError as exc:
        logger.error("Failed to embed texts (possibly missing OpenAI API key): %s", exc)
        raise EmbeddingUnavailableError(str(exc)) from exc
    session: Session = SessionLocal()
    saved: List[RAGDocument] = []
    try:
        for text_value, emb, meta in zip(texts, embeddings, metadatas):
            meta_dict = dict(meta or {})
            source_id = meta_dict.get("source_id")
            user_id = meta_dict.get("user_id")
            collection = collection_name

            doc = None
            if source_id and user_id:
                doc = (
                    session.query(RAGDocument)
                    .filter(RAGDocument.source_id == source_id, RAGDocument.user_id == user_id)
                    .first()
                )
            if doc is None:
                doc = RAGDocument()
                session.add(doc)

            doc.user_id = user_id or meta_dict.get("company_id") or meta_dict.get("owner_id")
            doc.title = meta_dict.get("title") or text_value[:80]
            doc.source_type = meta_dict.get("source_type") or "document"
            doc.source_id = source_id
            merged_meta = dict(meta_dict)
            merged_meta["collection"] = collection
            doc.metadata_json = merged_meta
            doc.content = text_value
            doc.embedding = emb
            saved.append(doc)

        session.commit()
        for d in saved:
            session.refresh(d)
        return saved
    finally:
        session.close()


async def similarity_search(
    collection_name: str,
    query: str,
    k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve top-k documents by cosine similarity within a collection.
    """
    try:
        query_emb_list = await embed_texts(query)
    except RuntimeError as exc:
        logger.error("Failed to embed query (possibly missing OpenAI API key): %s", exc)
        raise EmbeddingUnavailableError(str(exc)) from exc
    if not query_emb_list:
        return []
    query_emb = query_emb_list[0]

    session: Session = SessionLocal()
    try:
        q = session.query(RAGDocument)
        if filters and filters.get("user_id"):
            q = q.filter(RAGDocument.user_id == str(filters["user_id"]))
        docs: List[RAGDocument] = q.all()
    finally:
        session.close()

    scored: List[tuple[float, RAGDocument]] = []
    for doc in docs:
        meta = doc.metadata_json or {}
        if collection_name and meta.get("collection") != collection_name:
            continue
        if filters:
            # user_id filter: only exclude when both target and doc.user_id are present and unequal
            if filters.get("user_id") is not None and doc.user_id is not None:
                if str(doc.user_id) != str(filters["user_id"]):
                    continue
            # company_id filter: allow match against metadata company_id or doc.user_id; skip only when both exist and mismatch
            if filters.get("company_id") is not None:
                meta_company = meta.get("company_id")
                company_match = False
                if meta_company is not None and str(meta_company) == str(filters["company_id"]):
                    company_match = True
                if doc.user_id is not None and str(doc.user_id) == str(filters["company_id"]):
                    company_match = True
                if meta_company is not None or doc.user_id is not None:
                    if not company_match:
                        continue
            if filters.get("source_types"):
                source_val = meta.get("source_type") or doc.source_type
                if source_val and source_val not in filters["source_types"]:
                    continue

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
    for score, doc in scored[: max(k, 1)]:
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


async def fetch_recent_documents(
    limit: int = 5,
    user_id: Optional[str] = None,
    company_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch recent documents without embeddings; used for test-mode stubs.
    """
    session: Session = SessionLocal()
    try:
        q = session.query(RAGDocument).order_by(RAGDocument.created_at.desc())
        if user_id:
            q = q.filter(RAGDocument.user_id == user_id)
        if company_id:
            q = q.filter(RAGDocument.metadata_json.contains({"company_id": company_id}))
        docs: List[RAGDocument] = q.limit(max(limit, 1)).all()
    finally:
        session.close()

    return [
        {
            "id": doc.id,
            "title": doc.title,
            "text": doc.content,
            "metadata": doc.metadata_json or {},
            "score": 0.0,
        }
        for doc in docs
    ]


# Backward-compat wrappers
async def index_documents(documents: List[Dict[str, Any]], default_user_id: Optional[str] = None) -> List[RAGDocument]:
    texts: List[str] = []
    metas: List[Dict[str, Any]] = []
    for d in documents:
        texts.append(d.get("text") or "")
        meta = d.get("metadata") or {}
        if d.get("user_id") or default_user_id:
            meta["user_id"] = d.get("user_id") or default_user_id
        if d.get("source_id"):
            meta["source_id"] = d.get("source_id")
        meta.setdefault("collection", "global")
        meta.setdefault("title", d.get("title") or "")
        metas.append(meta)
    return await add_documents("global", texts, metas)


async def query_similar(
    question: str,
    k: int = 5,
    user_id: Optional[str] = None,
    company_id: Optional[str] = None,
    source_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    collection = f"company-{company_id}" if company_id else "global"
    filters: Dict[str, Any] = {}
    if user_id:
        filters["user_id"] = user_id
    if company_id:
        filters["company_id"] = company_id
    if source_types:
        filters["source_types"] = source_types
    return await similarity_search(collection, question, k=k, filters=filters)

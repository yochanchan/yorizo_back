from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence

from pymongo import MongoClient
from pymongo.collection import Collection

from app.core.config import settings
from app.core.openai_client import embed_texts

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "knowledge_chunks"
TOPK_CANDIDATES = 200
TOPK_RETURN = 5


def _get_collection() -> Optional[Collection]:
    uri = settings.cosmos_mongo_uri
    db_name = settings.cosmos_db_name
    coll_name = getattr(settings, "knowledge_collection", DEFAULT_COLLECTION) or DEFAULT_COLLECTION
    if not uri or not db_name:
        logger.warning("COSMOS_MONGO_URI or COSMOS_DB_NAME not set; skip knowledge search")
        return None
    client = MongoClient(uri)
    return client[db_name][coll_name]


def _project() -> Dict[str, int]:
    return {
        "_id": 1,
        "text": 1,
        "text_len": 1,
        "embedding": 1,
        "embedding_norm": 1,
        "source_title": 1,
        "source_path": 1,
        "page": 1,
        "chunk_index": 1,
    }


def _normalize(vec: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
    return [float(x) / norm for x in vec]


async def search_knowledge(query_text: str, top_k: int = TOPK_RETURN) -> List[Dict[str, Any]]:
    col = _get_collection()
    if col is None:
        return []

    embeddings = await embed_texts([query_text])
    if not embeddings:
        return []
    q_vec = _normalize(embeddings[0])

    candidates = list(col.find({}, projection=_project()).limit(TOPK_CANDIDATES))
    scored: List[Dict[str, Any]] = []
    missing_embed = 0
    zero_norm = 0
    for doc in candidates:
        emb = doc.get("embedding") or []
        if not emb:
            missing_embed += 1
            continue
        norm_val = float(doc.get("embedding_norm") or 0.0)
        if norm_val <= 0:
            zero_norm += 1
            norm_val = math.sqrt(sum(float(x) * float(x) for x in emb)) or 1.0
        doc_vec = [float(x) / norm_val for x in emb]
        dim = min(len(doc_vec), len(q_vec))
        score = sum(doc_vec[i] * q_vec[i] for i in range(dim))
        scored.append(
            {
                **doc,
                "score": score,
                "snippet": (doc.get("text") or "")[:400],
            }
        )

    scored.sort(key=lambda d: d.get("score", 0), reverse=True)
    top = scored[: top_k or TOPK_RETURN]
    if top:
        logger.info(
            "[knowledge] candidates=%s scored=%s missing_embed=%s zero_norm=%s top_score=%s title=%s page=%s",
            len(candidates),
            len(scored),
            missing_embed,
            zero_norm,
            top[0].get("score"),
            top[0].get("source_title"),
            top[0].get("page"),
        )
    else:
        logger.info(
            "[knowledge] candidates=%s scored=%s missing_embed=%s zero_norm=%s (no hits)",
            len(candidates),
            len(scored),
            missing_embed,
            zero_norm,
        )
    return top

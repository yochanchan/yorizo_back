"""
Ingest official PDFs under a root directory into Cosmos DB (Mongo API) as knowledge_chunks with embeddings.

Usage:
    python scripts/ingest_official_pdfs_with_embed.py ./data/pdfs
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import re
import unicodedata

from dotenv import load_dotenv
from pypdf import PdfReader
from pymongo import MongoClient, ReplaceOne

from app.core.openai_client import embed_texts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ingest_pdfs")

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
BATCH_EMBED = 32
DEFAULT_COLLECTION = "knowledge_chunks"
MIN_TEXT_LEN = 50
CONTROL_CHARS = "".join(chr(c) for c in range(0, 32) if c not in {9, 10, 13}) + chr(127)
CONTROL_RE = re.compile(f"[{re.escape(CONTROL_CHARS)}]")
MULTI_SPACE_RE = re.compile(r"[ \t]+")
MULTI_NL_RE = re.compile(r"\n{3,}")
PRINTABLE_RE = re.compile(r"[0-9A-Za-zぁ-んァ-ヶ一-龠々ー]")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_text(text: str) -> str:
    if not text:
        return ""
    t = CONTROL_RE.sub("", text)
    t = unicodedata.normalize("NFKC", t)
    t = MULTI_SPACE_RE.sub(" ", t)
    t = MULTI_NL_RE.sub("\n\n", t)
    t = t.strip()
    return t


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == n:
            break
        start = end - overlap
    return chunks


def _detect_org(filename: str) -> str:
    lower = filename.lower()
    if "hakusyo" in lower or "hakusho" in lower:
        return "中小企業庁（白書）"
    if "tebiki" in lower:
        return "中小企業庁（手引き）"
    return "その他"


def _extract_pdf_chunks(pdf_path: Path, root: Path) -> List[Dict[str, Any]]:
    rel_path = pdf_path.relative_to(root)
    source_path = rel_path.as_posix()
    doc_id = _hash(source_path)
    source_title = pdf_path.name
    source_org = _detect_org(pdf_path.name)

    chunks: List[Dict[str, Any]] = []
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        logger.exception("Failed to open PDF: %s", pdf_path)
        return chunks

    for page_idx, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            logger.exception("Failed to extract text: %s page=%s", pdf_path, page_idx + 1)
            continue
        raw_text = text
        text = _clean_text(text)
        if len(text) < MIN_TEXT_LEN:
            continue
        compact_len = len(re.sub(r"\s+", "", text))
        printable_ratio = len(PRINTABLE_RE.findall(text)) / max(compact_len, 1)
        if printable_ratio < 0.4:
            continue
        page_chunks = _chunk_text(text)
        for chunk_idx, chunk_text in enumerate(page_chunks):
            chunk_text = _clean_text(chunk_text)
            if len(chunk_text) < MIN_TEXT_LEN:
                continue
            chunk_id = _hash(f"{doc_id}-{page_idx+1}-{chunk_idx}-{_hash(chunk_text)}")
            chunks.append(
                {
                    "_id": chunk_id,
                    "doc_id": doc_id,
                    "source_title": source_title,
                    "source_path": source_path,
                    "source_org": source_org,
                    "page": page_idx + 1,
                    "chunk_index": chunk_idx,
                    "text": chunk_text,
                    "text_len": len(chunk_text),
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
    return chunks


async def _embed_and_upsert(chunks: List[Dict[str, Any]], collection) -> int:
    """
    Embed chunks in batches and upsert into Mongo.
    Returns number of upserted/modified docs.
    """
    upsert_count = 0
    for start in range(0, len(chunks), BATCH_EMBED):
        batch = chunks[start : start + BATCH_EMBED]
        texts = [c["text"] for c in batch]
        try:
            vectors = await embed_texts(texts)
            if len(vectors) != len(batch):
                logger.warning("Embedding count mismatch: texts=%s vectors=%s", len(batch), len(vectors))
                continue
            for doc, emb in zip(batch, vectors):
                doc["embedding"] = emb
                doc["embedding_norm"] = math.sqrt(sum(float(x) * float(x) for x in emb))
            ops = [ReplaceOne({"_id": c["_id"]}, c, upsert=True) for c in batch]
            result = collection.bulk_write(ops, ordered=False)
            upsert_count += result.upserted_count + result.modified_count
        except Exception:
            logger.exception("Embedding/upsert failed for batch starting with _id=%s", batch[0].get("_id"))
    return upsert_count


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root_dir", nargs="?", default="data/pdfs", help="Root directory to search PDFs")
    args = parser.parse_args()

    load_dotenv()
    root = Path(args.root_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Root dir not found: {root}")

    mongo_uri = os.getenv("COSMOS_MONGO_URI")
    db_name = os.getenv("COSMOS_DB_NAME")
    collection_name = os.getenv("KNOWLEDGE_COLLECTION", DEFAULT_COLLECTION)
    if not mongo_uri or not db_name:
        raise RuntimeError("COSMOS_MONGO_URI and COSMOS_DB_NAME are required")

    client = MongoClient(mongo_uri)
    collection = client[db_name][collection_name]

    pdf_limit = int(os.getenv("PDF_LIMIT") or 0) or None
    page_limit = int(os.getenv("PAGE_LIMIT") or 0) or None
    chunk_limit = int(os.getenv("CHUNK_LIMIT") or 0) or None

    pdf_files_all = list(root.rglob("*.pdf"))
    pdf_files = pdf_files_all[:pdf_limit] if pdf_limit else pdf_files_all
    if not pdf_files:
        logger.warning("No PDF files found under %s", root)
        return

    total_pages = 0
    total_chunks: List[Dict[str, Any]] = []
    failed_files = 0

    for pdf in pdf_files:
        file_chunks = _extract_pdf_chunks(pdf, root)
        if not file_chunks:
            failed_files += 1
        total_chunks.extend(file_chunks)
        # approximate pages from chunk metadata
        if file_chunks:
            total_pages += max(c["page"] for c in file_chunks)
        if page_limit and total_pages >= page_limit:
            logger.info("PAGE_LIMIT reached: %s", page_limit)
            break
        if chunk_limit and len(total_chunks) >= chunk_limit:
            logger.info("CHUNK_LIMIT reached: %s", chunk_limit)
            break

    logger.info(
        "PDFs: %s (limit=%s), pages (approx): %s (limit=%s), chunks: %s (limit=%s)",
        len(pdf_files),
        pdf_limit,
        total_pages,
        page_limit,
        len(total_chunks),
        chunk_limit,
    )
    if not total_chunks:
        logger.warning("No chunks to ingest.")
        return

    upserted = await _embed_and_upsert(total_chunks, collection)
    logger.info("Upserted/modified: %s", upserted)
    logger.info("Failed PDFs: %s", failed_files)
    logger.info("Collection count: %s", collection.count_documents({}))
    sample = collection.find_one({}, {"_id": 1, "source_title": 1, "page": 1, "text_len": 1})
    logger.info("Sample doc: %s", sample)


if __name__ == "__main__":
    asyncio.run(main())

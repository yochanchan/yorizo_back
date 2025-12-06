from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import List

from sqlalchemy.orm import Session

from app.core.config import settings
from app.rag.store import add_documents
from app.models import Document

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _extract_text(path: str, mime_type: str | None) -> str:
    suffix = Path(path).suffix.lower()
    content = _read_file_bytes(path)

    if suffix == ".pdf":
        try:
            import pypdf

            reader = pypdf.PdfReader(BytesIO(content))
            texts: List[str] = []
            for page in reader.pages:
                page_text = page.extract_text() or ""
                texts.append(page_text)
            return "\n".join(texts)
        except Exception:
            return "[PDFを読み込みましたがテキスト化に失敗しました]"

    if suffix in {".xlsx", ".xls"}:
        try:
            import pandas as pd  # type: ignore

            frames = pd.read_excel(BytesIO(content), sheet_name=None)
            parts: List[str] = []
            for name, df in frames.items():
                parts.append(f"[Sheet: {name}]")
                parts.append(df.astype(str).to_csv(index=False))
            return "\n".join(parts)
        except Exception:
            return "[スプレッドシートを読み込みましたがテキスト化に失敗しました]"

    if suffix in {".csv", ".tsv"}:
        try:
            text = content.decode("utf-8", errors="ignore")
            return text
        except Exception:
            return "[CSV/TSVを読み込みましたがテキスト化に失敗しました]"

    if mime_type and mime_type.startswith("image/"):
        return "[画像ファイルを受け取りました]"

    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return "[テキスト抽出できませんでした]"


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    if not text:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: List[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(start + chunk_size, length)
        chunks.append(normalized[start:end])
        if end == length:
            break
        start = max(end - overlap, start + 1)
    return chunks


async def ingest_document(db: Session, document: Document) -> None:
    if document.ingested:
        return
    if not document.storage_path or not os.path.exists(document.storage_path):
        return

    raw_text = _extract_text(document.storage_path, document.mime_type)
    chunks = _chunk_text(raw_text)
    if not chunks:
        chunks = ["[テキストを抽出できませんでしたが、資料を受け取りました]"]

    collection = "global" if not document.company_id else f"company-{document.company_id}"
    metadatas = []
    for chunk in chunks:
        metadatas.append(
            {
                "document_id": document.id,
                "company_id": document.company_id,
                "doc_type": document.doc_type,
                "period_label": document.period_label,
                "source_file": document.filename,
                "storage_path": document.storage_path,
                "collection": collection,
            }
        )

    await add_documents(collection, chunks, metadatas)
    document.ingested = True
    document.content_text = (raw_text[:4000] if raw_text else None)
    db.add(document)
    db.commit()
    db.refresh(document)


async def ingest_pending_documents(db: Session) -> int:
    pending = db.query(Document).filter(Document.ingested == False).all()  # noqa: E712
    count = 0
    for doc in pending:
        await ingest_document(db, doc)
        count += 1
    return count

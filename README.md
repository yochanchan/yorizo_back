# Yorizo Backend (FastAPI)

## Setup
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## Migrations
We use Alembic.
```bash
cd backend
alembic upgrade head  # uses DATABASE_URL (falls back to sqlite:///./yorizo.db)
```

## Run
```bash
uvicorn main:app --reload --port 8000
# http://localhost:8000/docs でAPI確認
```

### 開発用SQLiteをリセットする場合
古いスキーマとの不整合（例: `rag_documents.source_type` が無い等）があるときは、開発用 SQLite を作り直してください。
```powershell
cd backend
Remove-Item .\yorizo.db -ErrorAction SilentlyContinue  # Windows PowerShell
alembic upgrade head
uvicorn main:app --reload --port 8000
```
macOS/Linux の場合は `rm ./yorizo.db` を使ってください。

### RAG 動作確認手順
```bash
cd backend
# .env に OPENAI_API_KEY / OPENAI_MODEL_CHAT / OPENAI_MODEL_EMBEDDING を設定
alembic upgrade head
uvicorn main:app --reload --port 8000
# http://127.0.0.1:8000/docs を開き、以下の順で確認
# 1) POST /api/rag/documents で登録
# 2) POST /api/rag/search で類似検索
# 3) POST /api/rag/chat でコンテキスト付き回答
```

## Environment variables
- `DATABASE_URL`: full SQLAlchemy URL (optional; overrides DB_*), e.g. `sqlite:///./yorizo.db` or `mysql+pymysql://user:pass@host:3306/yorizo`  
  - If you supply an async driver (e.g., `mysql+asyncmy` or `sqlite+aiosqlite`), it will be normalized to a sync driver for the current engine.
- `DB_HOST`: default `localhost`
- `DB_PORT`: default `3306`
- `DB_USERNAME`: use this instead of a reserved `username` key in Azure App Service
- `DB_PASSWORD`
- `DB_NAME`
- `OPENAI_API_KEY`: OpenAI key
- `OPENAI_MODEL_CHAT`: default `gpt-4.1-mini`
- `OPENAI_MODEL_EMBEDDING`: default `text-embedding-3-small`
- `CORS_ORIGINS`: CSV of allowed origins (default `http://localhost:3000`)

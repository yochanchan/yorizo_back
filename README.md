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

## Official PDF knowledge (RAG)
- PDFファイルはリポジトリに含めず、`backend/data/pdfs/` などローカルに置く（.gitignore 済み）。
- Cosmos に投入するには、事前に .env で `COSMOS_MONGO_URI` / `COSMOS_DB_NAME` を設定し、必要に応じて `KNOWLEDGE_COLLECTION` を指定。
- 少量テスト例:
  ```bash
  cd backend
  set PDF_LIMIT=2
  set PAGE_LIMIT=5
  set CHUNK_LIMIT=30
  python scripts/ingest_official_pdfs_with_embed.py ./data/pdfs
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

## 本番 MySQL のスキーマ更新
デプロイ前に MySQL でも Alembic を流してスキーマを合わせてください（手動 SQL は不要）。
```bash
cd backend
export DATABASE_URL="mysql+pymysql://user:pass@host:3306/yorizo?charset=utf8mb4"  # 実環境に置き換え
alembic upgrade head
```

## Environment variables
- `APP_ENV`: `local` なら SQLite を使います。Azure など本番/ステージングでは `production` をセットし、必ず `DATABASE_URL` もしくは `DB_*` を設定してください。
- `DATABASE_URL`: full SQLAlchemy URL (optional; overrides DB_*), e.g. `sqlite:///./yorizo.db` or `mysql+pymysql://user:pass@host:3306/yorizo`  
  - If you supply an async driver (e.g., `mysql+asyncmy` or `sqlite+aiosqlite`), it will be normalized to a sync driver for the current engine.
- `DB_HOST`: default `localhost`
- `DB_PORT`: default `3306`
- `DB_USERNAME`: use this instead of a reserved `username` key in Azure App Service
- `DB_PASSWORD`
- `DB_NAME`
- `DB_SSL_CA`: MySQL SSL の CA パス（省略時 `/etc/ssl/certs/ca-certificates.crt`）。Azure Database for MySQL は `DigiCertGlobalRootG2.crt.pem` などを指定してください。
- `OPENAI_API_KEY`: OpenAI key
- `OPENAI_MODEL_CHAT`: default `gpt-4.1-mini`
- `OPENAI_MODEL_EMBEDDING`: default `text-embedding-3-small`
- `AZURE_OPENAI_ENDPOINT`: e.g. `https://aoai-10th.openai.azure.com/`
- `AZURE_OPENAI_API_KEY`: Azure OpenAI key
- `AZURE_OPENAI_CHAT_DEPLOYMENT`: deployment name used for chat (e.g., `gpt-4o-mini-yorizo`)
- `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`: deployment name used for embeddings (required for RAG)
  - (フォールバックで `AZURE_OPENAI_DEPLOYMENT` も読み取りますが、今後は上記を設定してください)
- `AZURE_OPENAI_API_VERSION`: default `2024-02-15-preview`
- `CORS_ORIGINS`: CSV of allowed origins (default `http://localhost:3000`)

# Yorizo Backend (FastAPI)

## Setup
```bash
cd back
python -m venv .venv
.venv/Scripts/activate  # Windows の場合
pip install -r requirements.txt
```

## Migrations (Alembic)
スキーマ変更は Alembic マイグレーションのみで管理します。  
ローカル・ステージング・本番のいずれも、MySQL に対して `alembic upgrade head` を実行してスキーマを適用します。

```bash
cd back
# 事前に .env または環境変数で DATABASE_URL もしくは DB_* を設定しておく
alembic upgrade head
```

## Run
```bash
cd back
uvicorn main:app --reload --port 8000
# http://localhost:8000/docs で API ドキュメントを確認できます
```

### RAG セットアップの例
```bash
cd back
# .env に OPENAI_API_KEY / OPENAI_MODEL_CHAT / OPENAI_MODEL_EMBEDDING などを設定
alembic upgrade head
uvicorn main:app --reload --port 8000
# http://127.0.0.1:8000/docs から以下を順に確認
# 1) POST /api/rag/documents でドキュメントを登録
# 2) POST /api/rag/search で検索
# 3) POST /api/rag/chat でチャット連携
```

## MySQL 接続と Alembic 実行の例
ローカル・ステージング・本番いずれも MySQL を利用し、スキーマ変更は Alembic マイグレーションで管理します。

```bash
cd back
export DATABASE_URL="mysql+pymysql://user:pass@host:3306/yorizo?charset=utf8mb4"  # 値は環境に合わせて変更
alembic upgrade head
```

## Environment variables
- `APP_ENV`: `local` / `development` / `production` / `staging` / `azure` など。DB 接続そのものは `DATABASE_URL` もしくは `DB_*` で必ず指定する。
- `DATABASE_URL`: full SQLAlchemy URL (optional; overrides DB_*), e.g. `mysql+pymysql://user:pass@host:3306/yorizo?charset=utf8mb4`  
  - If you supply an async driver (e.g., `mysql+asyncmy`), it will be normalized to a sync driver for the current engine.
- `DB_HOST`: default `localhost`
- `DB_PORT`: default `3306`
- `DB_USERNAME`: use this instead of a reserved `username` key in Azure App Service
- `DB_PASSWORD`
- `DB_NAME`
- `DB_SSL_CA`: MySQL SSL 用の CA 証明書パス。`/etc/ssl/certs/ca-certificates.crt` など。Azure Database for MySQL では `DigiCertGlobalRootG2.crt.pem` などを指定。
- `OPENAI_API_KEY`: OpenAI key
- `OPENAI_MODEL_CHAT`: default `gpt-4.1-mini`
- `OPENAI_MODEL_EMBEDDING`: default `text-embedding-3-small`
- `AZURE_OPENAI_ENDPOINT`: e.g. `https://aoai-10th.openai.azure.com/`
- `AZURE_OPENAI_API_KEY`: Azure OpenAI key
- `AZURE_OPENAI_CHAT_DEPLOYMENT`: deployment name used for chat (e.g., `gpt-4o-mini-yorizo`)
  - （古い環境では `AZURE_OPENAI_DEPLOYMENT` を使っている場合もあるため、validation_alias で両方を受け付けます）
- `AZURE_OPENAI_API_VERSION`: default `2024-02-15-preview`
- `CORS_ORIGINS`: CSV of allowed origins (default `http://localhost:3000`)


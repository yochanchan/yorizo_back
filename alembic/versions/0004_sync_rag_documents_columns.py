"""sync rag_documents columns to current model

Revision ID: 0004_sync_rag_documents_columns
Revises: 0003_add_rag_documents
Create Date: 2025-11-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_sync_rag_documents_columns"
down_revision = "0003_add_rag_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("rag_documents"):
        return

    cols = {col["name"] for col in inspector.get_columns("rag_documents")}

    if "source_type" not in cols:
        op.add_column(
            "rag_documents",
            sa.Column("source_type", sa.String(length=50), nullable=False, server_default="manual"),
        )
    if "source_id" not in cols:
        op.add_column("rag_documents", sa.Column("source_id", sa.String(length=255), nullable=True))
    if "metadata" not in cols:
        op.add_column("rag_documents", sa.Column("metadata", sa.JSON(), nullable=True))
    if "embedding" not in cols:
        op.add_column("rag_documents", sa.Column("embedding", sa.JSON(), nullable=True))
    if "created_at" not in cols:
        op.add_column(
            "rag_documents",
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    if "updated_at" not in cols:
        op.add_column(
            "rag_documents",
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    # Downgrade removes only columns we added conditionally if they exist.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("rag_documents"):
        return

    cols = {col["name"] for col in inspector.get_columns("rag_documents")}
    for col_name in [
        "updated_at",
        "created_at",
        "embedding",
        "metadata",
        "source_id",
        "source_type",
    ]:
        if col_name in cols:
            op.drop_column("rag_documents", col_name)

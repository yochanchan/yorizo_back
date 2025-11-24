"""add rag documents table

Revision ID: 0003_add_rag_documents
Revises: 0002_homework_tasks
Create Date: 2025-11-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_add_rag_documents"
down_revision = "0002_homework_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("rag_documents"):
        op.create_table(
            "rag_documents",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(length=255), nullable=True),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("source_type", sa.String(length=50), nullable=False, server_default="system"),
            sa.Column("source_id", sa.String(length=255), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("embedding", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=False,
            ),
        )
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("rag_documents")} if inspector.has_table("rag_documents") else set()
    if "ix_rag_documents_user_id" not in existing_indexes and inspector.has_table("rag_documents"):
        op.create_index("ix_rag_documents_user_id", "rag_documents", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_rag_documents_user_id", table_name="rag_documents")
    op.drop_table("rag_documents")

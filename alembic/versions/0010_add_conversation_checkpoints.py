"""add conversation checkpoints and turn_count

Revision ID: 0010_add_conversation_checkpoints
Revises: 0009_add_financial_statement_document_link
Create Date: 2025-12-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0010_add_conversation_checkpoints"
down_revision = "0009_add_financial_statement_document_link"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    # conversations.turn_count を idempotent に追加
    conv_cols = [c["name"] for c in insp.get_columns("conversations")]
    if "turn_count" not in conv_cols:
        op.add_column("conversations", sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"))
        # サーバーデフォルトを外す（SQLiteではスキップ）
        if conn.dialect.name != "sqlite":
            op.alter_column("conversations", "turn_count", server_default=None)

    # conversation_checkpoints テーブルを idempotent に作成
    if "conversation_checkpoints" not in insp.get_table_names():
        op.create_table(
            "conversation_checkpoints",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.String(length=36), nullable=False, index=True),
            sa.Column("idx", sa.Integer(), nullable=False),
            sa.Column("turn_count", sa.Integer(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
            sa.UniqueConstraint("conversation_id", "idx", name="uq_conversation_checkpoint_idx"),
            sa.UniqueConstraint("conversation_id", "turn_count", name="uq_conversation_checkpoint_turn_count"),
        )


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if "conversation_checkpoints" in insp.get_table_names():
        op.drop_constraint("uq_conversation_checkpoint_turn_count", "conversation_checkpoints", type_="unique")
        op.drop_constraint("uq_conversation_checkpoint_idx", "conversation_checkpoints", type_="unique")
        op.drop_table("conversation_checkpoints")

    conv_cols = [c["name"] for c in insp.get_columns("conversations")]
    if "turn_count" in conv_cols:
        op.drop_column("conversations", "turn_count")

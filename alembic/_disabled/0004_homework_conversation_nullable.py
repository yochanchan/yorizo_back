"""make homework conversation nullable and set null on delete

Revision ID: 0004_homework_conversation_nullable
Revises: 0003_add_rag_documents
Create Date: 2025-12-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_homework_conversation_nullable"
down_revision: Union[str, None] = "0003_add_rag_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    fk_name = None
    for fk in inspector.get_foreign_keys("homework_tasks"):
        if "conversation_id" in fk.get("constrained_columns", []):
            fk_name = fk["name"]
            break

    with op.batch_alter_table("homework_tasks") as batch_op:
        if fk_name:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        batch_op.alter_column("conversation_id", existing_type=sa.String(length=36), nullable=True)
        batch_op.create_foreign_key(
            "homework_tasks_conversation_id_fkey",
            "conversations",
            ["conversation_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    fk_name = None
    for fk in inspector.get_foreign_keys("homework_tasks"):
        if "conversation_id" in fk.get("constrained_columns", []):
            fk_name = fk["name"]
            break

    with op.batch_alter_table("homework_tasks") as batch_op:
        if fk_name:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        batch_op.alter_column("conversation_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.create_foreign_key(
            "homework_tasks_conversation_id_fkey",
            "conversations",
            ["conversation_id"],
            ["id"],
        )

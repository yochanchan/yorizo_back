"""merge heads to unify branches

Revision ID: 0011_merge_heads
Revises: 0010_add_conversation_checkpoints, 0004_homework_conversation_nullable
Create Date: 2025-12-14
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_merge_heads"
down_revision = ("0010_add_conversation_checkpoints", "0004_homework_conversation_nullable")
branch_labels = None
depends_on = None


def upgrade():
    # No-op merge migration.
    pass


def downgrade():
    # No-op merge migration.
    pass

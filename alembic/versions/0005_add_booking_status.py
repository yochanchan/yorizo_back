"""Add status to consultation_bookings

Revision ID: 0005_add_booking_status
Revises: 0004_sync_rag_documents_columns
Create Date: 2025-11-26
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_add_booking_status"
down_revision = "0004_sync_rag_documents_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = [col["name"] for col in inspector.get_columns("consultation_bookings")]
    if "status" not in existing:
        op.add_column(
            "consultation_bookings",
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        )
        op.alter_column("consultation_bookings", "status", server_default=None)


def downgrade() -> None:
    op.drop_column("consultation_bookings", "status")

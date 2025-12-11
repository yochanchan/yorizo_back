"""Add document link and period columns to financial_statements

Revision ID: 0009_financial_statement_document_link
Revises: 0008_financial_statement_fields
Create Date: 2025-12-09
"""

from alembic import op
import sqlalchemy as sa

from app.models.base import GUID_LENGTH


revision = "0009_financial_statement_document_link"
down_revision = "0008_financial_statement_fields"
branch_labels = None
depends_on = None


def _column_exists(inspector: sa.Inspector, table: str, column: str) -> bool:
    return any(col.get("name") == column for col in inspector.get_columns(table))


def _constraint_exists(inspector: sa.Inspector, table: str, name: str) -> bool:
    return any(cons.get("name") == name for cons in inspector.get_unique_constraints(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_statements" not in inspector.get_table_names():
        return

    if not _column_exists(inspector, "financial_statements", "document_id"):
        op.add_column(
            "financial_statements",
            sa.Column("document_id", sa.String(GUID_LENGTH), sa.ForeignKey("documents.id"), nullable=True),
        )
    if not _column_exists(inspector, "financial_statements", "period_start"):
        op.add_column("financial_statements", sa.Column("period_start", sa.Date(), nullable=True))
    if not _column_exists(inspector, "financial_statements", "period_end"):
        op.add_column("financial_statements", sa.Column("period_end", sa.Date(), nullable=True))
    if not _constraint_exists(inspector, "financial_statements", "uq_financial_statements_document_id"):
        try:
            op.create_unique_constraint(
                "uq_financial_statements_document_id",
                "financial_statements",
                ["document_id"],
            )
        except Exception:
            # Best-effort; skip if backend does not support adding the constraint
            pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_statements" not in inspector.get_table_names():
        return

    if _constraint_exists(inspector, "financial_statements", "uq_financial_statements_document_id"):
        try:
            op.drop_constraint("uq_financial_statements_document_id", "financial_statements", type_="unique")
        except Exception:
            pass

    for col in ["period_end", "period_start", "document_id"]:
        if _column_exists(inspector, "financial_statements", col):
            op.drop_column("financial_statements", col)

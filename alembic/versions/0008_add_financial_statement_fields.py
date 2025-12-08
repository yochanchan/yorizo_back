"""add total_assets and interest_bearing_debt columns to financial_statements

Revision ID: 0008_financial_statement_fields
Revises: 0007_add_meeting_links
Create Date: 2025-12-08
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_financial_statement_fields"
down_revision = "0007_add_meeting_links"
branch_labels = None
depends_on = None


def _column_exists(inspector: sa.Inspector, table: str, column: str) -> bool:
    return any(col.get("name") == column for col in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_statements" not in inspector.get_table_names():
        return

    if not _column_exists(inspector, "financial_statements", "total_assets"):
        op.add_column("financial_statements", sa.Column("total_assets", sa.Numeric(18, 2), nullable=True))
    if not _column_exists(inspector, "financial_statements", "equity"):
        op.add_column("financial_statements", sa.Column("equity", sa.Numeric(18, 2), nullable=True))
    if not _column_exists(inspector, "financial_statements", "total_liabilities"):
        op.add_column("financial_statements", sa.Column("total_liabilities", sa.Numeric(18, 2), nullable=True))
    if not _column_exists(inspector, "financial_statements", "interest_bearing_debt"):
        op.add_column("financial_statements", sa.Column("interest_bearing_debt", sa.Numeric(18, 2), nullable=True))
    if not _column_exists(inspector, "financial_statements", "previous_sales"):
        op.add_column("financial_statements", sa.Column("previous_sales", sa.Numeric(18, 2), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "financial_statements" not in inspector.get_table_names():
        return

    for col in ["previous_sales", "interest_bearing_debt", "total_liabilities", "equity", "total_assets"]:
        if _column_exists(inspector, "financial_statements", col):
            op.drop_column("financial_statements", col)

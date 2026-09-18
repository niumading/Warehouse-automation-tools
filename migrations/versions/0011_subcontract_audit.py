"""record subcontract audit user

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subcontract_orders", sa.Column("audited_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_subcontract_audited_by", "subcontract_orders", "users", ["audited_by"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_subcontract_audited_by", "subcontract_orders", type_="foreignkey")
    op.drop_column("subcontract_orders", "audited_by")

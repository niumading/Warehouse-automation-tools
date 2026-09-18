"""Subcontract rejection and resubmission history.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subcontract_orders", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.add_column("subcontract_orders", sa.Column("rejected_by", sa.BigInteger(), nullable=True))
    op.add_column("subcontract_orders", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subcontract_orders", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("subcontract_orders", sa.Column("review_history", sa.JSON(), nullable=False, server_default="[]"))
    op.create_foreign_key("fk_subcontract_rejected_by", "subcontract_orders", "users", ["rejected_by"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_subcontract_rejected_by", "subcontract_orders", type_="foreignkey")
    for column in ("review_history", "revision", "rejected_at", "rejected_by", "rejection_reason"):
        op.drop_column("subcontract_orders", column)

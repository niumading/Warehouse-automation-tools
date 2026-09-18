"""add idempotent ERP push state

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_orders",
        sa.Column("erp_push_status", sa.String(), nullable=False, server_default="not_pushed"),
    )
    op.add_column(
        "purchase_orders",
        sa.Column("erp_push_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("purchase_orders", sa.Column("erp_last_error", sa.Text(), nullable=True))
    op.add_column(
        "purchase_orders",
        sa.Column("erp_pushed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_orders", "erp_pushed_at")
    op.drop_column("purchase_orders", "erp_last_error")
    op.drop_column("purchase_orders", "erp_push_attempts")
    op.drop_column("purchase_orders", "erp_push_status")

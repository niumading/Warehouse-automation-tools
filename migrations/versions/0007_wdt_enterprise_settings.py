"""add WangDianTong enterprise seller account

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_settings", sa.Column("erp_sid", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_settings", "erp_sid")

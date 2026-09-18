"""add tenant ERP endpoint and token settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_settings", sa.Column("erp_base_url", sa.String(), nullable=True))
    op.add_column("tenant_settings", sa.Column("erp_token", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_settings", "erp_token")
    op.drop_column("tenant_settings", "erp_base_url")

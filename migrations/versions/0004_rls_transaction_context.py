"""make RLS safe across transaction boundaries

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = (
    "users",
    "suppliers",
    "products",
    "product_supplier",
    "warehouses",
    "purchase_orders",
    "purchase_order_items",
    "ocr_records",
    "tenant_settings",
)

SAFE_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::bigint"


def _alter_policies(expression: str) -> None:
    for table in TENANT_TABLES:
        op.execute(
            f"ALTER POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {expression}) "
            f"WITH CHECK (tenant_id = {expression})"
        )


def upgrade() -> None:
    # PostgreSQL 自定义变量在事务结束后可能表现为空串；NULLIF 使其安全地匹配零行。
    _alter_policies(SAFE_TENANT)


def downgrade() -> None:
    _alter_policies("current_setting('app.tenant_id', true)::bigint")

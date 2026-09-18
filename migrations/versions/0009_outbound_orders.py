"""add photo-created outbound orders

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SAFE_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::bigint"


def upgrade() -> None:
    op.execute("CREATE SEQUENCE outbound_order_no_seq START WITH 1")
    op.create_table(
        "outbound_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_no", sa.String(), nullable=False),
        sa.Column("outbound_type", sa.String(), nullable=False, server_default="other"),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("destination_warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("department", sa.String(), nullable=True),
        sa.Column("recipient", sa.String(), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_audit"),
        sa.Column("source", sa.String(), nullable=False, server_default="ocr"),
        sa.Column("image_path", sa.String(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("audited_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "order_no", name="uq_outbound_tenant_no"),
    )
    op.create_index("ix_outbound_orders_tenant_id", "outbound_orders", ["tenant_id"])
    op.create_index("ix_outbound_orders_tenant_created", "outbound_orders", ["tenant_id", "created_at"])
    op.create_table(
        "outbound_order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("outbound_orders.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(), nullable=False),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("qty", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("remark", sa.String(), nullable=True),
    )
    op.create_index("ix_outbound_order_items_tenant_id", "outbound_order_items", ["tenant_id"])
    op.create_index("ix_outbound_order_items_order_id", "outbound_order_items", ["tenant_id", "order_id"])
    for table in ("outbound_orders", "outbound_order_items"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {SAFE_TENANT}) WITH CHECK (tenant_id = {SAFE_TENANT})"
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.add_column("ocr_records", sa.Column("document_type", sa.String(), nullable=False, server_default="purchase"))
    op.add_column(
        "ocr_records",
        sa.Column("outbound_order_id", sa.BigInteger(), sa.ForeignKey("outbound_orders.id"), nullable=True),
    )
    op.create_index("ix_ocr_records_outbound_order_id", "ocr_records", ["outbound_order_id"])


def downgrade() -> None:
    op.drop_index("ix_ocr_records_outbound_order_id", table_name="ocr_records")
    op.drop_column("ocr_records", "outbound_order_id")
    op.drop_column("ocr_records", "document_type")
    op.drop_table("outbound_order_items")
    op.drop_table("outbound_orders")
    op.execute("DROP SEQUENCE outbound_order_no_seq")

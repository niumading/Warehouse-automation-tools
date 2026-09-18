"""add subcontract inbound and outbound orders

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SAFE_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::bigint"


def upgrade() -> None:
    op.execute("CREATE SEQUENCE subcontract_order_no_seq START WITH 1")
    op.create_table(
        "subcontract_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_no", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_audit"),
        sa.Column("source", sa.String(), nullable=False, server_default="ocr"),
        sa.Column("image_path", sa.String(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("audited_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_subcontract_direction"),
        sa.UniqueConstraint("tenant_id", "order_no", name="uq_subcontract_tenant_no"),
    )
    op.create_index("ix_subcontract_orders_tenant_id", "subcontract_orders", ["tenant_id"])
    op.create_index(
        "ix_subcontract_orders_tenant_created", "subcontract_orders", ["tenant_id", "created_at"]
    )
    op.create_table(
        "subcontract_order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("subcontract_orders.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(), nullable=False),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("qty", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("remark", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_subcontract_order_items_tenant_id", "subcontract_order_items", ["tenant_id"]
    )
    op.create_index(
        "ix_subcontract_order_items_order_id",
        "subcontract_order_items",
        ["tenant_id", "order_id"],
    )
    op.add_column(
        "ocr_records",
        sa.Column(
            "subcontract_order_id",
            sa.BigInteger(),
            sa.ForeignKey("subcontract_orders.id"),
            nullable=True,
        ),
    )
    for table in ("subcontract_orders", "subcontract_order_items"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {SAFE_TENANT}) WITH CHECK (tenant_id = {SAFE_TENANT})"
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_column("ocr_records", "subcontract_order_id")
    op.drop_table("subcontract_order_items")
    op.drop_table("subcontract_orders")
    op.execute("DROP SEQUENCE subcontract_order_no_seq")

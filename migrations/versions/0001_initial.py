"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===== 租户 =====
    op.create_table(
        "tenants",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ===== 用户 =====
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("is_master", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # ===== 供应商 =====
    op.create_table(
        "suppliers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("return_addr", sa.String(), nullable=True),
        sa.Column("contact", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_supplier_tenant_name"),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])

    # ===== 商品 =====
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("spec", sa.String(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])

    # ===== 商品-供应商关联 =====
    op.create_table(
        "product_supplier",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.UniqueConstraint("tenant_id", "product_id", "supplier_id", name="uq_ps_tenant"),
    )
    op.create_index("ix_product_supplier_tenant_id", "product_supplier", ["tenant_id"])

    # ===== 仓库 =====
    op.create_table(
        "warehouses",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "code", name="uq_warehouse_tenant_code"),
    )
    op.create_index("ix_warehouses_tenant_id", "warehouses", ["tenant_id"])

    # ===== 采购入库单 =====
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_no", sa.String(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouses.id"), nullable=True),
        sa.Column("status", sa.String(), server_default=sa.text("'pending_audit'")),
        sa.Column("source", sa.String(), server_default=sa.text("'manual'")),
        sa.Column("image_path", sa.String(), nullable=True),
        sa.Column("erp_order_id", sa.String(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("audited_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "order_no", name="uq_po_tenant_no"),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])

    # ===== 采购入库单明细 =====
    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("purchase_orders.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("product_name", sa.String(), nullable=True),
        sa.Column("sku", sa.String(), nullable=True),
        sa.Column("qty", sa.Numeric(12, 2), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
    )
    op.create_index("ix_purchase_order_items_tenant_id", "purchase_order_items", ["tenant_id"])

    # ===== 识别记录 =====
    op.create_table(
        "ocr_records",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("purchase_orders.id"), nullable=True),
        sa.Column("image_path", sa.String(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("fields_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ocr_records_tenant_id", "ocr_records", ["tenant_id"])

    # ===== 系统设置 =====
    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("auto_audit_enabled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("audit_threshold", sa.Numeric(5, 4), server_default=sa.text("0.95")),
        sa.Column("erp_type", sa.String(), nullable=True),
        sa.Column("erp_appkey", sa.String(), nullable=True),
        sa.Column("erp_appsecret", sa.String(), nullable=True),
    )

    # ===== 单据号序列（并发安全，跨租户唯一）=====
    op.execute("CREATE SEQUENCE IF NOT EXISTS purchase_order_no_seq START 1")

    # ===== 启用 RLS 行级安全（租户隔离双保险，ADR-09）=====
    for table in ["suppliers", "products", "product_supplier", "warehouses",
                  "purchase_orders", "purchase_order_items", "ocr_records"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::bigint)"
        )
    # users / tenant_settings 也做隔离
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON users "
        "USING (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS purchase_order_no_seq")
    for table in ["ocr_records", "purchase_order_items", "purchase_orders",
                  "warehouses", "product_supplier", "products", "suppliers",
                  "users", "tenant_settings", "tenants"]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

"""add intelligent return waybill imports

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_settings", sa.Column("return_import_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        "return_import_records",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("tracking_no", sa.String(), nullable=False),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("qty", sa.Numeric(12, 2), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("product_name", sa.String(), nullable=True),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("supplier_code", sa.String(), nullable=True),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("return_addr", sa.String(), nullable=True),
        sa.Column("source_filename", sa.String(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "tracking_no", "sku", name="uq_return_record_tracking_sku"),
    )
    op.create_index("ix_return_import_records_tenant_id", "return_import_records", ["tenant_id"])
    op.create_index("ix_return_import_records_tracking_no", "return_import_records", ["tenant_id", "tracking_no"])
    op.execute("ALTER TABLE return_import_records ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON return_import_records "
        "USING (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    op.execute("ALTER TABLE return_import_records FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("return_import_records")
    op.drop_column("tenant_settings", "return_import_mapping")

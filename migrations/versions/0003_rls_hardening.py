"""force tenant RLS and add controlled login lookup

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
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


def upgrade() -> None:
    # 登录名必须全局唯一，才能在尚无租户上下文时安全定位账号。
    op.create_index("uq_users_username_global", "users", ["username"], unique=True)

    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant_settings "
        "USING (tenant_id = current_setting('app.tenant_id', true)::bigint)"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # 函数由迁移管理员拥有，普通应用账号只能执行，不能直接绕过 RLS 查询 users。
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.zhidan_auth_lookup(p_username text)
        RETURNS TABLE(
            id bigint,
            tenant_id bigint,
            username varchar,
            password_hash varchar,
            role varchar,
            is_master boolean,
            is_active boolean
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $function$
            SELECT u.id, u.tenant_id, u.username, u.password_hash,
                   u.role, u.is_master, u.is_active
            FROM public.users AS u
            WHERE u.username = p_username
            LIMIT 1
        $function$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.zhidan_auth_lookup(text) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.zhidan_auth_lookup(text)")
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenant_settings")
    op.execute("ALTER TABLE tenant_settings DISABLE ROW LEVEL SECURITY")
    op.drop_index("uq_users_username_global", table_name="users")

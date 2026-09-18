"""创建受限应用账号、授权并安全更新 .env。不会输出任何数据库密码。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import secrets
import shutil

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

from app.core.config import get_settings

ROLE_NAME = "zhidan_app"
PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_DIR / ".env"


def _replace_env_value(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = prefix + value
            break
    else:
        lines.append(prefix + value)
    return "\n".join(lines) + "\n"


def main() -> None:
    if not ENV_PATH.exists():
        raise RuntimeError("未找到项目 .env")

    settings = get_settings()
    admin_sa_url = settings.database_admin_url or settings.database_url
    parsed = make_url(admin_sa_url)
    if not parsed.database:
        raise RuntimeError("DATABASE_URL 缺少数据库名")

    password = secrets.token_urlsafe(32)
    admin_pg_url = admin_sa_url.replace("+psycopg2", "")
    with psycopg2.connect(admin_pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cur.fetchone()[0]:
                raise RuntimeError("需要使用 PostgreSQL 超级账号执行数据库加固")
            cur.execute(
                "SELECT username FROM users GROUP BY username HAVING count(*) > 1 LIMIT 1"
            )
            if cur.fetchone():
                raise RuntimeError("存在重复登录名，无法启用全局唯一登录")

            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE_NAME,))
            role_exists = cur.fetchone() is not None
            role_ident = sql.Identifier(ROLE_NAME)
            password_literal = sql.Literal(password)
            if role_exists:
                cur.execute(
                    sql.SQL(
                        "ALTER ROLE {} WITH LOGIN PASSWORD {} "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                    ).format(role_ident, password_literal)
                )
            else:
                cur.execute(
                    sql.SQL(
                        "CREATE ROLE {} WITH LOGIN PASSWORD {} "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                    ).format(role_ident, password_literal)
                )

            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(parsed.database), role_ident
                )
            )
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role_ident))
            cur.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
                ).format(role_ident)
            )
            cur.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    role_ident
                )
            )
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(role_ident)
            )
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(role_ident)
            )
            cur.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION public.zhidan_auth_lookup(text) TO {}").format(
                    role_ident
                )
            )

    app_sa_url = parsed.set(username=ROLE_NAME, password=password)
    app_url_text = app_sa_url.render_as_string(hide_password=False)
    app_pg_url = app_url_text.replace("+psycopg2", "")

    # 写配置前验证账号权限与 RLS：租户 1 可见，切到不存在租户后必须为 0。
    with psycopg2.connect(app_pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_user, rolsuper, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname = current_user"
            )
            role_state = cur.fetchone()
            if role_state != (ROLE_NAME, False, False, False):
                raise RuntimeError("受限账号权限验证失败")
            cur.execute("SELECT set_config('app.tenant_id', '999999999', true)")
            cur.execute("SELECT count(*) FROM users")
            if cur.fetchone()[0] != 0:
                raise RuntimeError("RLS 隔离验证失败")
            cur.execute("SELECT id FROM public.zhidan_auth_lookup(%s)", ("admin",))
            if cur.fetchone() is None:
                raise RuntimeError("受控登录查询验证失败")

    backup_dir = PROJECT_DIR / "backups" / f"db-role-{datetime.now():%Y%m%d-%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(ENV_PATH, backup_dir / ".env")

    original = ENV_PATH.read_text(encoding="utf-8")
    updated = _replace_env_value(original, "DATABASE_ADMIN_URL", admin_sa_url)
    updated = _replace_env_value(updated, "DATABASE_URL", app_url_text)
    temp_path = ENV_PATH.with_suffix(".env.new")
    temp_path.write_text(updated, encoding="utf-8")
    temp_path.replace(ENV_PATH)

    print(f"OK: 已启用受限数据库账号 {ROLE_NAME}")
    print("OK: RLS 隔离与登录函数验证通过")
    print(f"OK: 原配置已备份到 {backup_dir}")


if __name__ == "__main__":
    main()

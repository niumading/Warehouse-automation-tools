"""受限数据库账号与 RLS 加固的回归测试。"""
from pathlib import Path
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.auth import _find_login_user
from app.core.database import Base
from app.models import Tenant, User


ROOT = Path(__file__).resolve().parents[1]


class RlsHardeningTest(unittest.TestCase):
    def test_sqlite_login_lookup_keeps_test_and_local_fallback(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(Tenant(id=1, name="测试客户"))
            db.add(User(id=1, tenant_id=1, username="admin", password_hash="x", role="admin"))
            db.commit()
            self.assertEqual(_find_login_user(db, "admin").tenant_id, 1)
            self.assertIsNone(_find_login_user(db, "missing"))
        engine.dispose()

    def test_migration_forces_all_tenant_tables_and_secures_login_function(self):
        migration = (ROOT / "migrations/versions/0003_rls_hardening.py").read_text(encoding="utf-8")
        for table in (
            "users", "suppliers", "products", "product_supplier", "warehouses",
            "purchase_orders", "purchase_order_items", "ocr_records", "tenant_settings",
        ):
            self.assertIn(f'"{table}"', migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("SECURITY DEFINER", migration)
        self.assertIn("REVOKE ALL ON FUNCTION", migration)

    def test_migrations_use_admin_url_after_runtime_account_switch(self):
        migration_env = (ROOT / "migrations/env.py").read_text(encoding="utf-8")
        self.assertIn("settings.database_admin_url or settings.database_url", migration_env)

    def test_rls_policy_handles_empty_database_setting(self):
        migration = (ROOT / "migrations/versions/0004_rls_transaction_context.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("NULLIF(current_setting('app.tenant_id', true), '')::bigint", migration)

    def test_outbound_migration_forces_rls_and_extends_ocr_records(self):
        migration = (ROOT / "migrations/versions/0009_outbound_orders.py").read_text(
            encoding="utf-8"
        )
        for table in ("outbound_orders", "outbound_order_items"):
            self.assertIn(f'"{table}"', migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("NULLIF(current_setting('app.tenant_id', true), '')::bigint", migration)
        self.assertIn('"document_type"', migration)
        self.assertIn('"outbound_order_id"', migration)

    def test_subcontract_migration_forces_rls_and_extends_ocr_records(self):
        migration = (ROOT / "migrations/versions/0010_subcontract_orders.py").read_text(
            encoding="utf-8"
        )
        for table in ("subcontract_orders", "subcontract_order_items"):
            self.assertIn(f'"{table}"', migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("NULLIF(current_setting('app.tenant_id', true), '')::bigint", migration)
        self.assertIn('"subcontract_order_id"', migration)


if __name__ == "__main__":
    unittest.main()

"""RLS 租户上下文必须在同一 Session 的每个新事务自动恢复。"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from app.core.database import TenantSession, set_tenant_context


class RlsTransactionContextTest(unittest.TestCase):
    def test_tenant_context_is_reapplied_after_commit(self):
        engine = create_engine("sqlite://")
        session = TenantSession(bind=engine)
        try:
            set_tenant_context(session, 7)
            with patch("app.core.database._inject_rls_tenant") as inject:
                session.execute(text("SELECT 1"))
                session.commit()
                session.execute(text("SELECT 1"))
                self.assertEqual(inject.call_count, 2)
                self.assertEqual([call.args[1] for call in inject.call_args_list], [7, 7])
        finally:
            session.close()
            engine.dispose()

    def test_missing_tenant_context_does_not_inject(self):
        engine = create_engine("sqlite://")
        session = TenantSession(bind=engine)
        try:
            with patch("app.core.database._inject_rls_tenant") as inject:
                session.execute(text("SELECT 1"))
                inject.assert_not_called()
        finally:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

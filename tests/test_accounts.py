"""子账号、租户隔离与权限分级测试。"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.core.security import hash_password
from app.main import app
from app.models import Tenant, User


def data(response):
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([Tenant(id=1, name="客户一"), Tenant(id=2, name="客户二")])
        self.master = User(id=1, tenant_id=1, username="master", password_hash=hash_password("secret1"), role="admin", is_master=True, is_active=True)
        self.other = User(id=2, tenant_id=2, username="other", password_hash=hash_password("secret2"), role="admin", is_master=True, is_active=True)
        self.db.add_all([self.master, self.other]); self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.master
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear(); self.client.close(); self.db.close(); self.engine.dispose()

    def test_master_creates_lists_updates_and_disables_subaccount(self):
        response = self.client.post("/api/accounts", json={"username": "buyer1", "password": "test-only-password", "role": "operator"})
        self.assertEqual(response.status_code, 201, response.text)
        account = data(response)
        self.assertNotIn("password", response.text)
        listed = data(self.client.get("/api/accounts"))
        self.assertEqual([row["username"] for row in listed], ["master", "buyer1"])
        updated = data(self.client.put(f"/api/accounts/{account['id']}", json={"role": "auditor", "is_active": False}))
        self.assertEqual(updated["role"], "auditor")
        self.assertFalse(updated["is_active"])

    def test_non_master_and_cross_tenant_account_are_blocked(self):
        self.master.role = "operator"; self.master.is_master = False
        self.assertEqual(self.client.get("/api/accounts").status_code, 403)
        self.master.role = "admin"; self.master.is_master = True
        self.assertEqual(self.client.put("/api/accounts/2", json={"is_active": False}).status_code, 404)
        self.assertEqual(self.client.put("/api/accounts/1", json={"is_active": False}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

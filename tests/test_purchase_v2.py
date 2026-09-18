"""新版实际 HTTP 路由测试，使用独立的内存数据库，不修改业务库。"""
import itertools
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.core.ai_config import AiConfig
from app.main import app
from app.models import Product, PurchaseOrderItem, Supplier, Tenant, TenantSetting, User, Warehouse


def data(response):
    body = response.json()
    return body.get("data", body)


class PurchaseV2Test(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([Tenant(id=1, name="测试客户"), Tenant(id=2, name="另一客户")])
        self.db.flush()
        self.user = User(id=1, tenant_id=1, username="test", password_hash="unused", role="operator")
        self.db.add(self.user)
        for tid in (1, 2):
            self.db.add(Supplier(id=tid, tenant_id=tid, name=f"供应商{tid}"))
            self.db.add(Warehouse(id=tid, tenant_id=tid, name=f"仓库{tid}", code=f"WH{tid}"))
            self.db.add(Product(id=tid, tenant_id=tid, name=f"商品{tid}", sku=f"SKU{tid}", unit="盒"))
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        counter = itertools.count(1)
        self.sequence = patch("app.api.purchase._generate_order_no", side_effect=lambda *_: f"TEST{next(counter):06}")
        self.sequence.start()

    def tearDown(self):
        self.sequence.stop()
        app.dependency_overrides.clear()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def payload(self, qty=10):
        return {"source": "manual", "supplier_id": 1, "warehouse_id": 1,
                "items": [{"product_id": 1, "qty": qty, "price": 12.5}]}

    def create(self):
        response = self.client.post("/api/purchase/orders", json=self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        return data(response)["id"]

    def test_create_detail_edit_audit_return(self):
        oid = self.create()
        detail = data(self.client.get(f"/api/purchase/orders/{oid}"))
        self.assertEqual(detail["items"][0]["product_name"], "商品1")
        self.assertEqual(detail["items"][0]["price"], 12.5)
        body = self.payload(15)
        body["items"].append({"product_id": 1, "qty": 3, "price": 9.5})
        self.assertEqual(self.client.put(f"/api/purchase/orders/{oid}", json=body).status_code, 200)
        detail = data(self.client.get(f"/api/purchase/orders/{oid}"))
        self.assertEqual([i["qty"] for i in detail["items"]], [15, 3])
        self.assertEqual(self.client.post(f"/api/purchase/orders/{oid}/audit").status_code, 200)
        self.assertEqual(self.client.put(f"/api/purchase/orders/{oid}", json=body).status_code, 400)
        self.assertEqual(self.client.post(f"/api/purchase/orders/{oid}/audit").status_code, 400)
        self.assertEqual(self.client.post(f"/api/purchase/orders/{oid}/return").status_code, 200)
        self.assertEqual(self.client.put(f"/api/purchase/orders/{oid}", json=self.payload(20)).status_code, 200)
        self.assertEqual(data(self.client.get(f"/api/purchase/orders/{oid}"))["items"][0]["qty"], 20)

    def test_rejects_cross_tenant_master_and_detail(self):
        for field in ("supplier_id", "warehouse_id"):
            body = self.payload(); body[field] = 2
            self.assertEqual(self.client.post("/api/purchase/orders", json=body).status_code, 400)
        body = self.payload(); body["items"][0]["product_id"] = 2
        self.assertEqual(self.client.post("/api/purchase/orders", json=body).status_code, 400)
        oid = self.create()
        self.db.add(PurchaseOrderItem(tenant_id=2, order_id=oid, product_name="不能泄露", qty=1))
        self.db.commit()
        self.assertEqual(len(data(self.client.get(f"/api/purchase/orders/{oid}"))["items"]), 1)
        self.user.tenant_id = 2
        self.assertEqual(self.client.get(f"/api/purchase/orders/{oid}").status_code, 404)

    def test_rejects_invalid_lines_and_viewer_writes(self):
        for qty in (0, -1):
            self.assertEqual(self.client.post("/api/purchase/orders", json=self.payload(qty)).status_code, 422)
        body = self.payload(); body["items"] = []
        self.assertEqual(self.client.post("/api/purchase/orders", json=body).status_code, 422)
        oid = self.create(); self.user.role = "viewer"
        self.assertEqual(self.client.get(f"/api/purchase/orders/{oid}").status_code, 200)
        self.assertEqual(self.client.post("/api/purchase/orders", json=self.payload()).status_code, 403)
        self.assertEqual(self.client.put(f"/api/purchase/orders/{oid}", json=self.payload()).status_code, 403)
        self.assertEqual(self.client.post(f"/api/purchase/orders/{oid}/audit").status_code, 403)

    def test_auditor_can_audit_but_cannot_create_or_edit(self):
        oid = self.create()
        self.user.role = "auditor"
        self.assertEqual(self.client.post("/api/purchase/orders", json=self.payload()).status_code, 403)
        self.assertEqual(self.client.put(f"/api/purchase/orders/{oid}", json=self.payload()).status_code, 403)
        self.assertEqual(self.client.post(f"/api/purchase/orders/{oid}/audit").status_code, 200)
        self.assertEqual(self.client.post(f"/api/purchase/orders/{oid}/return").status_code, 200)

    def test_both_frontends_and_auth_boundary(self):
        self.assertEqual(self.client.get("/console/").status_code, 200)
        page = self.client.get("/console-v2/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("单据中心", page.text)
        for asset in ("app.js", "api.js", "theme.css", "workspace.css"):
            self.assertEqual(self.client.get(f"/console-v2/{asset}").status_code, 200)
        app.dependency_overrides.pop(get_current_user)
        self.assertEqual(self.client.get("/api/purchase/orders").status_code, 401)

    def test_ai_settings_are_admin_only_and_never_echo_secret(self):
        current = AiConfig("https://api.example.com/v1", "vision-model", "sk-super-secret")
        updated = AiConfig("https://new.example.com/v1", "vision-v2", "sk-new-secret")
        with patch("app.api.settings.get_ai_config", return_value=current), patch(
            "app.api.settings.save_ai_config", return_value=updated
        ) as save:
            response = self.client.get("/api/settings")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("sk-super-secret", response.text)
            self.assertEqual(data(response)["ai_api_key_hint"], "••••cret")

            self.assertEqual(self.client.put("/api/settings", json={"ai_vision_model": "blocked"}).status_code, 403)
            self.user.role = "admin"
            response = self.client.put("/api/settings", json={
                "ai_base_url": "https://new.example.com/v1/",
                "ai_vision_model": "vision-v2",
                "ai_api_key": "sk-new-secret",
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("sk-new-secret", response.text)
            save.assert_called_once_with(
                base_url="https://new.example.com/v1",
                vision_model="vision-v2",
                api_key="sk-new-secret",
            )
            self.assertEqual(self.client.put("/api/settings", json={"ai_base_url": "ftp://bad"}).status_code, 422)

    def test_erp_settings_are_masked_and_blank_keeps_secrets(self):
        self.user.role = "admin"
        response = self.client.put("/api/settings", json={
            "erp_type": "gjp",
            "erp_base_url": "https://ngp.example.com/api/",
            "erp_appkey": "app-key-1234",
            "erp_appsecret": "app-secret-5678",
            "erp_token": "erp-token-9012",
        })
        self.assertEqual(response.status_code, 200, response.text)
        for secret in ("app-key-1234", "app-secret-5678", "erp-token-9012"):
            self.assertNotIn(secret, response.text)
        public = data(response)
        self.assertTrue(public["erp_configured"])
        self.assertEqual(public["erp_appkey_hint"], "••••1234")
        self.assertEqual(public["erp_appsecret_hint"], "••••5678")
        self.assertEqual(public["erp_token_hint"], "••••9012")
        self.assertEqual(public["erp_base_url"], "https://ngp.example.com/api")

        response = self.client.put("/api/settings", json={
            "erp_appkey": "", "erp_appsecret": "", "erp_token": "",
        })
        self.assertEqual(response.status_code, 200, response.text)
        setting = self.db.get(TenantSetting, 1)
        self.assertEqual(setting.erp_appkey, "app-key-1234")
        self.assertEqual(setting.erp_appsecret, "app-secret-5678")
        self.assertEqual(setting.erp_token, "erp-token-9012")
        self.assertEqual(self.client.put("/api/settings", json={"erp_base_url": "file://bad"}).status_code, 422)

    def test_wdt_settings_use_sid_without_token(self):
        self.user.role = "admin"
        response = self.client.put("/api/settings", json={
            "erp_type": "wdt_qyb",
            "erp_base_url": "https://sandbox.wangdian.cn/openapi2/",
            "erp_sid": "seller-1234",
            "erp_appkey": "wdt-key-1234",
            "erp_appsecret": "wdt-secret-5678",
        })
        self.assertEqual(response.status_code, 200, response.text)
        public = data(response)
        self.assertTrue(public["erp_configured"])
        self.assertEqual(public["erp_sid_hint"], "••••1234")
        self.assertNotIn("seller-1234", response.text)


if __name__ == "__main__":
    unittest.main()

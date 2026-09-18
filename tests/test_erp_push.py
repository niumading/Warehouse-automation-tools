"""ERP 推送状态、重试与幂等回归测试。"""
import itertools
import json
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models import Product, PurchaseOrder, Supplier, Tenant, TenantSetting, User, Warehouse
from app.services.erp import (
    ErpGatewayError,
    ErpNotConfigured,
    ErpPushResult,
    WdtEnterpriseGateway,
    build_wdt_purchase_info,
    build_wdt_signature,
)


def data(response):
    body = response.json()
    return body.get("data", body)


class FakeGateway:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def push_purchase_order(self, payload, idempotency_key):
        self.calls.append((payload, idempotency_key))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return ErpPushResult(outcome)


class ErpPushTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(Tenant(id=1, name="测试客户"))
        self.user = User(id=1, tenant_id=1, username="operator", password_hash="x", role="operator")
        self.db.add(self.user)
        self.db.add(TenantSetting(tenant_id=1, erp_type="gjp", erp_appkey="key", erp_appsecret="secret"))
        self.db.add(Supplier(id=1, tenant_id=1, name="供应商", code="SUP-1"))
        self.db.add(Warehouse(id=1, tenant_id=1, name="仓库", code="WH-1"))
        self.db.add(Product(id=1, tenant_id=1, name="商品", sku="SKU-1", unit="盒"))
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        counter = itertools.count(1)
        self.sequence = patch("app.api.purchase._generate_order_no", side_effect=lambda *_: f"ERP{next(counter):06}")
        self.sequence.start()

    def tearDown(self):
        self.sequence.stop()
        app.dependency_overrides.clear()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def create_order(self, audit=True):
        response = self.client.post("/api/purchase/orders", json={
            "source": "manual", "supplier_id": 1, "warehouse_id": 1,
            "items": [{"product_id": 1, "qty": 2, "price": 12.5}],
        })
        order_id = data(response)["id"]
        if audit:
            self.client.post(f"/api/purchase/orders/{order_id}/audit")
        return order_id

    def test_success_and_repeat_are_idempotent(self):
        order_id = self.create_order()
        gateway = FakeGateway(["REMOTE-100"])
        with patch("app.api.purchase.get_erp_gateway", return_value=gateway):
            first = self.client.post(f"/api/purchase/orders/{order_id}/push-erp")
            second = self.client.post(f"/api/purchase/orders/{order_id}/push-erp")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(data(first)["reused"])
        self.assertTrue(data(second)["reused"])
        self.assertEqual(data(second)["erp_push_attempts"], 1)
        self.assertEqual(len(gateway.calls), 1)
        payload, key = gateway.calls[0]
        self.assertEqual(key, data(first)["order_no"])
        self.assertEqual(payload["business_order_no"], key)
        self.assertEqual(payload["items"][0]["qty"], "2.00")
        self.assertEqual(
            self.client.post(f"/api/purchase/orders/{order_id}/return").status_code,
            400,
        )

    def test_failed_push_can_retry_with_same_key(self):
        order_id = self.create_order()
        gateway = FakeGateway([ErpGatewayError("temporary"), "REMOTE-200"])
        with patch("app.api.purchase.get_erp_gateway", return_value=gateway):
            failed = self.client.post(f"/api/purchase/orders/{order_id}/push-erp")
            retried = self.client.post(f"/api/purchase/orders/{order_id}/push-erp")
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(data(retried)["erp_push_attempts"], 2)
        self.assertEqual(gateway.calls[0][1], gateway.calls[1][1])

    def test_only_audited_orders_can_push(self):
        order_id = self.create_order(audit=False)
        gateway = FakeGateway(["unused"])
        with patch("app.api.purchase.get_erp_gateway", return_value=gateway):
            response = self.client.post(f"/api/purchase/orders/{order_id}/push-erp")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(gateway.calls, [])


class WdtEnterpriseGatewayTest(unittest.TestCase):
    def test_signature_matches_official_example(self):
        params = {
            "appkey": "test2-xx",
            "page_no": "0",
            "end_time": "2016-08-01 13:00:00",
            "start_time": "2016-08-01 12:00:00",
            "page_size": "40",
            "sid": "test2",
            "timestamp": "1470042310",
        }
        self.assertEqual(build_wdt_signature(params, "12345"), "ad4e6fe037ea6e3ba4768317be9d1309")

    def test_purchase_payload_maps_stable_business_fields(self):
        payload = {
            "supplier": {"code": "SUP-1"},
            "warehouse": {"code": "WH-1"},
            "items": [{"sku": "SKU-1", "qty": "2.00", "price": "12.50"}],
        }
        result = build_wdt_purchase_info(payload, "LOCAL-1")
        self.assertEqual(result["provider_no"], "SUP-1")
        self.assertEqual(result["warehouse_no"], "WH-1")
        self.assertEqual(result["outer_no"], "LOCAL-1")
        self.assertEqual(result["details_list"][0], {"spec_no": "SKU-1", "num": "2.00", "price": "12.50"})

    def test_push_posts_signed_form_and_returns_purchase_number(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "message": "CG202609080001"}
        gateway = WdtEnterpriseGateway(
            "https://sandbox.wangdian.cn/openapi2", "seller", "key", "secret"
        )
        payload = {
            "supplier": {"code": "SUP-1"},
            "warehouse": {"code": "WH-1"},
            "items": [{"sku": "SKU-1", "qty": "2.00", "price": "12.50"}],
        }
        with patch("app.services.erp.time.time", return_value=1470042310), patch(
            "app.services.erp.httpx.post", return_value=response
        ) as post:
            result = gateway.push_purchase_order(payload, "LOCAL-1")
        self.assertEqual(result.external_order_id, "CG202609080001")
        self.assertEqual(post.call_args.args[0], "https://sandbox.wangdian.cn/openapi2/purchase_order_push.php")
        form = post.call_args.kwargs["data"]
        self.assertEqual(form["sid"], "seller")
        self.assertEqual(json.loads(form["purchase_info"])["outer_no"], "LOCAL-1")
        unsigned = {key: value for key, value in form.items() if key != "sign"}
        self.assertEqual(form["sign"], build_wdt_signature(unsigned, "secret"))

    def test_rejects_non_official_gateway_host(self):
        with self.assertRaises(ErpNotConfigured):
            WdtEnterpriseGateway("https://example.com/openapi2", "seller", "key", "secret")


if __name__ == "__main__":
    unittest.main()

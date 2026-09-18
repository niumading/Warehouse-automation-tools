"""其他出库拍照制单的 HTTP 与数据隔离测试。"""
import itertools
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models import (
    OcrRecord, OutboundOrder, OutboundOrderItem, Product, Tenant, User, Warehouse,
)


def data(response):
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


class OutboundTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([Tenant(id=1, name="测试客户"), Tenant(id=2, name="另一客户")])
        self.db.flush()
        self.user = User(
            id=1, tenant_id=1, username="operator", password_hash="unused", role="operator"
        )
        self.db.add(self.user)
        self.db.add_all([
            Warehouse(id=1, tenant_id=1, name="主仓", code="WH1"),
            Warehouse(id=2, tenant_id=1, name="二仓", code="WH2"),
            Warehouse(id=3, tenant_id=2, name="外部仓", code="OTHER"),
            Product(id=1, tenant_id=1, name="工作服", sku="SKU-1", unit="件"),
            Product(id=2, tenant_id=2, name="外部商品", sku="SKU-2", unit="件"),
        ])
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        counter = itertools.count(1)
        self.sequence = patch(
            "app.api.outbound._generate_order_no",
            side_effect=lambda *_: f"CKTEST{next(counter):06d}",
        )
        self.sequence.start()

    def tearDown(self):
        self.sequence.stop()
        app.dependency_overrides.clear()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def payload(**overrides):
        body = {
            "source": "manual",
            "outbound_type": "requisition",
            "warehouse_id": 1,
            "department": "运营部",
            "recipient": "张三",
            "items": [{"product_id": 1, "qty": 3, "remark": "日常领用"}],
        }
        body.update(overrides)
        return body

    def test_create_list_and_detail_are_tenant_scoped(self):
        response = self.client.post("/api/outbound/orders", json=self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        order = data(response)
        self.assertEqual(order["status"], "pending_audit")
        self.assertEqual(order["source"], "manual")

        listed = data(self.client.get("/api/outbound/orders"))
        self.assertEqual([row["id"] for row in listed], [order["id"]])
        detail = data(self.client.get(f"/api/outbound/orders/{order['id']}"))
        self.assertEqual(detail["items"][0]["product_name"], "工作服")
        self.assertEqual(detail["items"][0]["sku"], "SKU-1")
        self.assertEqual(detail["items"][0]["qty"], 3)

        self.db.add(OutboundOrderItem(
            tenant_id=2, order_id=order["id"], product_id=2,
            product_name="不能泄露", sku="SKU-2", qty=1,
        ))
        self.db.commit()
        detail = data(self.client.get(f"/api/outbound/orders/{order['id']}"))
        self.assertEqual(len(detail["items"]), 1)
        self.user.tenant_id = 2
        self.assertEqual(self.client.get(f"/api/outbound/orders/{order['id']}").status_code, 404)

    def test_rejects_cross_tenant_and_invalid_transfer(self):
        self.assertEqual(
            self.client.post("/api/outbound/orders", json=self.payload(warehouse_id=3)).status_code,
            400,
        )
        cross_product = self.payload()
        cross_product["items"][0]["product_id"] = 2
        self.assertEqual(self.client.post("/api/outbound/orders", json=cross_product).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/outbound/orders", json=self.payload(outbound_type="transfer")
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/outbound/orders",
                json=self.payload(outbound_type="transfer", destination_warehouse_id=1),
            ).status_code,
            400,
        )
        response = self.client.post(
            "/api/outbound/orders",
            json=self.payload(outbound_type="transfer", destination_warehouse_id=2),
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_ocr_commit_is_idempotent_and_forces_ocr_source(self):
        record = OcrRecord(
            tenant_id=1,
            document_type="outbound",
            image_path="outbound.jpg",
            fields_json={"status": "recognized", "recognized": {"items": []}},
        )
        self.db.add(record)
        self.db.commit()
        first = self.client.post(
            f"/api/outbound/ocr/{record.id}/commit", json=self.payload(source="manual")
        )
        second = self.client.post(
            f"/api/outbound/ocr/{record.id}/commit", json=self.payload(source="manual")
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(data(first)["reused"])
        self.assertTrue(data(second)["reused"])
        self.assertEqual(data(first)["order"]["source"], "ocr")
        self.assertEqual(self.db.query(OutboundOrder).count(), 1)
        self.db.refresh(record)
        self.assertEqual(record.outbound_order_id, data(first)["order"]["id"])

    def test_viewer_can_read_but_cannot_create_or_commit(self):
        created = self.client.post("/api/outbound/orders", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        self.user.role = "viewer"
        self.assertEqual(self.client.get("/api/outbound/orders").status_code, 200)
        self.assertEqual(
            self.client.post("/api/outbound/orders", json=self.payload()).status_code, 403
        )


if __name__ == "__main__":
    unittest.main()

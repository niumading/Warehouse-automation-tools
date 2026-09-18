"""委外入库/出库的创建、隔离与 OCR 幂等提交测试。"""
import itertools
import unittest
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models import (
    OcrRecord,
    Product,
    SubcontractOrder,
    Supplier,
    Tenant,
    User,
    Warehouse,
)


def data(response):
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


class SubcontractTest(unittest.TestCase):
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
            Supplier(id=1, tenant_id=1, name="加工商 A", code="JG-A"),
            Supplier(id=2, tenant_id=2, name="外部加工商", code="JG-X"),
            Warehouse(id=1, tenant_id=1, name="主仓", code="WH1"),
            Warehouse(id=2, tenant_id=2, name="外部仓", code="WHX"),
            Product(id=1, tenant_id=1, name="委外商品", sku="SKU-1", unit="件"),
            Product(id=2, tenant_id=2, name="外部商品", sku="SKU-X", unit="件"),
        ])
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        counter = itertools.count(1)
        self.sequence = patch(
            "app.api.subcontract._generate_order_no",
            side_effect=lambda _db, _tenant, direction: (
                "WWRK" if direction == "inbound" else "WWCK"
            ) + f"TEST{next(counter):06d}",
        )
        self.sequence.start()

    def tearDown(self):
        self.sequence.stop()
        app.dependency_overrides.clear()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def payload(direction="inbound", **overrides):
        body = {
            "source": "manual",
            "direction": direction,
            "supplier_id": 1,
            "warehouse_id": 1,
            "remark": "委外加工",
            "items": [{"product_id": 1, "qty": 8, "remark": "按照片核对"}],
        }
        body.update(overrides)
        return body

    def test_create_both_directions_and_filter(self):
        inbound = self.client.post("/api/subcontract/orders", json=self.payload("inbound"))
        outbound = self.client.post("/api/subcontract/orders", json=self.payload("outbound"))
        self.assertEqual(inbound.status_code, 201, inbound.text)
        self.assertEqual(outbound.status_code, 201, outbound.text)
        self.assertTrue(data(inbound)["order_no"].startswith("WWRK"))
        self.assertTrue(data(outbound)["order_no"].startswith("WWCK"))
        for response in (inbound, outbound):
            self.assertEqual(data(response)["source"], "manual")
            self.assertEqual(data(response)["status"], "pending_audit")
            self.assertIsNone(self.db.get(SubcontractOrder, data(response)["id"]).image_path)
        listed = data(self.client.get("/api/subcontract/orders?direction=inbound"))
        self.assertEqual(len(listed), 1)
        detail = data(self.client.get(f"/api/subcontract/orders/{data(inbound)['id']}"))
        self.assertEqual(detail["items"][0]["product_name"], "委外商品")
        self.assertEqual(detail["items"][0]["qty"], 8)

    def test_rejects_cross_tenant_master_data(self):
        self.assertEqual(
            self.client.post(
                "/api/subcontract/orders", json=self.payload(supplier_id=2)
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/subcontract/orders", json=self.payload(warehouse_id=2)
            ).status_code,
            400,
        )
        body = self.payload()
        body["items"][0]["product_id"] = 2
        self.assertEqual(self.client.post("/api/subcontract/orders", json=body).status_code, 400)

    def test_ocr_commit_is_idempotent_and_direction_scoped(self):
        record = OcrRecord(
            tenant_id=1,
            document_type="subcontract_inbound",
            image_path="subcontract.jpg",
            fields_json={"status": "recognized", "direction": "inbound", "recognized": {"items": []}},
        )
        self.db.add(record)
        self.db.commit()
        wrong = self.client.post(
            f"/api/subcontract/ocr/{record.id}/commit", json=self.payload("outbound")
        )
        self.assertEqual(wrong.status_code, 404)
        first = self.client.post(
            f"/api/subcontract/ocr/{record.id}/commit", json=self.payload("inbound")
        )
        second = self.client.post(
            f"/api/subcontract/ocr/{record.id}/commit", json=self.payload("inbound")
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(data(first)["reused"])
        self.assertTrue(data(second)["reused"])
        self.assertEqual(self.db.query(SubcontractOrder).count(), 1)

    def test_viewer_can_read_but_cannot_create(self):
        created = self.client.post("/api/subcontract/orders", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        self.user.role = "viewer"
        self.assertEqual(self.client.get("/api/subcontract/orders").status_code, 200)
        self.assertEqual(
            self.client.post("/api/subcontract/orders", json=self.payload()).status_code, 403
        )

    def test_audit_both_directions_records_actor_and_rejects_repeat(self):
        for direction in ("inbound", "outbound"):
            created = data(self.client.post("/api/subcontract/orders", json=self.payload(direction)))
            self.user.role = "auditor"
            response = self.client.post(f"/api/subcontract/orders/{created['id']}/audit", json={"revision":created["revision"]})
            self.assertEqual(response.status_code, 200, response.text)
            audited = data(response)
            self.assertEqual(audited["status"], "audited")
            self.assertEqual(audited["audited_by"], self.user.id)
            self.assertIsNotNone(audited["audited_at"])
            detail = data(self.client.get(f"/api/subcontract/orders/{created['id']}"))
            self.assertEqual(detail["audited_by_name"], self.user.username)
            repeated = self.client.post(f"/api/subcontract/orders/{created['id']}/audit", json={"revision":created["revision"]})
            self.assertEqual(repeated.status_code, 400)
            self.assertEqual(self.db.get(SubcontractOrder, created["id"]).status, "audited")
            self.user.role = "operator"

    def test_audit_is_tenant_scoped_and_viewer_forbidden(self):
        created = data(self.client.post("/api/subcontract/orders", json=self.payload()))
        self.user.role = "viewer"
        response = self.client.post(f"/api/subcontract/orders/{created['id']}/audit", json={"revision":created["revision"]})
        self.assertEqual(response.status_code, 403)
        self.user.role = "auditor"
        self.user.tenant_id = 2
        response = self.client.post(f"/api/subcontract/orders/{created['id']}/audit", json={"revision":created["revision"]})
        self.assertEqual(response.status_code, 404)
        self.user.tenant_id = 1
        self.assertEqual(self.db.get(SubcontractOrder, created["id"]).status, "pending_audit")


    def test_audited_export_print_both_directions_and_read_only_role(self):
        ns = {"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for direction in ("inbound","outbound"):
            created = data(self.client.post("/api/subcontract/orders",json=self.payload(direction)))
            self.client.post(f"/api/subcontract/orders/{created['id']}/audit",json={"revision":created["revision"]})
            self.user.role = "viewer"
            response = self.client.get(f"/api/subcontract/orders/{created['id']}/export")
            self.assertEqual(response.status_code,200,response.text[:100])
            self.assertIn("filename*=UTF-8''",response.headers["content-disposition"])
            self.assertEqual(response.headers["cache-control"],"no-store")
            with ZipFile(BytesIO(response.content)) as book:
                for name in book.namelist(): ET.fromstring(book.read(name))
                sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
                self.assertEqual(sheet.find("s:pageSetup",ns).get("orientation"),"landscape")
                cells = {c.get("r"):c for c in sheet.findall(".//s:c",ns)}
                self.assertEqual(cells["B8"].get("t"),"inlineStr")
                self.assertEqual(cells["D8"].get("t"),"n")
                self.assertEqual(cells["D8"].find("s:v",ns).text,"8")
                self.assertEqual(cells["A1"].find("s:is/s:t",ns).text,"委外入库单" if direction=="inbound" else "委外出库单")
            printed = self.client.get(f"/api/subcontract/orders/{created['id']}/print")
            self.assertEqual(printed.status_code,200)
            for value in (created["order_no"],"加工商 A","主仓","SKU-1","operator","A4 landscape"):
                self.assertIn(value,printed.text)
            self.assertEqual(data(self.client.get(f"/api/subcontract/orders/{created['id']}"))["status"],"audited")
            self.assertEqual(self.db.get(SubcontractOrder,created["id"]).revision,2)
            self.user.role = "operator"

    def test_document_output_requires_audit_and_tenant_and_auth(self):
        created = data(self.client.post("/api/subcontract/orders",json=self.payload()))
        for suffix in ("export","print"):
            self.assertEqual(self.client.get(f"/api/subcontract/orders/{created['id']}/{suffix}").status_code,409)
        rejected = data(self.reject(created))
        for suffix in ("export","print"):
            self.assertEqual(self.client.get(f"/api/subcontract/orders/{created['id']}/{suffix}").status_code,409)
        pending=data(self.resubmit(rejected))
        self.client.post(f"/api/subcontract/orders/{pending['id']}/audit",json={"revision":pending["revision"]})
        self.user.tenant_id=2
        for suffix in ("export","print"):
            self.assertEqual(self.client.get(f"/api/subcontract/orders/{created['id']}/{suffix}").status_code,404)
        app.dependency_overrides.pop(get_current_user)
        for suffix in ("export","print"):
            self.assertEqual(self.client.get(f"/api/subcontract/orders/{created['id']}/{suffix}").status_code,401)

    def test_document_escapes_html_and_excel_formula_like_text(self):
        product=self.db.get(Product,1); product.name='<script>alert(1)</script>'; product.sku='001234'
        self.db.commit()
        created=data(self.client.post("/api/subcontract/orders",json=self.payload(remark='<img src=x onerror=alert(1)>',items=[{"product_id":1,"qty":1.25,"remark":"=HYPERLINK(\"https://example.com\")"}])))
        self.client.post(f"/api/subcontract/orders/{created['id']}/audit",json={"revision":created["revision"]})
        printed=self.client.get(f"/api/subcontract/orders/{created['id']}/print")
        self.assertNotIn('<script>',printed.text); self.assertNotIn('<img',printed.text)
        self.assertIn('&lt;script&gt;',printed.text)
        with ZipFile(BytesIO(self.client.get(f"/api/subcontract/orders/{created['id']}/export").content)) as book:
            sheet=ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
            ns={"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            self.assertFalse(sheet.findall('.//s:f',ns))
            cells={c.get('r'):c for c in sheet.findall('.//s:c',ns)}
            self.assertEqual(cells['B8'].find('s:is/s:t',ns).text,'001234')
            self.assertEqual(cells['D8'].find('s:v',ns).text,'1.25')
            self.assertEqual(cells['F8'].get('t'),'inlineStr')

    def reject(self, order, reason="数量有误"):
        return self.client.post(f"/api/subcontract/orders/{order['id']}/reject",
                                json={"reason": reason, "revision": order["revision"]})

    def resubmit(self, order, **overrides):
        body = self.payload(**overrides)
        body.pop("source"); body.pop("direction")
        body["revision"] = order["revision"]
        return self.client.post(f"/api/subcontract/orders/{order['id']}/resubmit", json=body)

    def test_reject_resubmit_both_directions_preserves_identity_and_history(self):
        for direction in ("inbound", "outbound"):
            created = data(self.client.post("/api/subcontract/orders", json=self.payload(direction)))
            response = self.reject(created, "  数量有误  ")
            self.assertEqual(response.status_code, 200, response.text)
            rejected = data(response)
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(rejected["rejection_reason"], "数量有误")
            self.assertEqual(rejected["rejected_by"], self.user.id)
            self.assertIsNotNone(rejected["rejected_at"])
            self.assertEqual(self.client.post(f"/api/subcontract/orders/{created['id']}/audit",json={"revision":created["revision"]}).status_code, 400)
            res = self.resubmit(rejected, items=[{"product_id": 1, "qty": 10}])
            self.assertEqual(res.status_code, 200, res.text)
            pending = data(res)
            self.assertEqual(pending["id"], created["id"])
            self.assertEqual(pending["order_no"], created["order_no"])
            self.assertEqual(pending["direction"], direction)
            self.assertEqual(pending["status"], "pending_audit")
            detail = data(self.client.get(f"/api/subcontract/orders/{pending['id']}"))
            self.assertEqual(len(detail["items"]), 1)
            self.assertEqual(detail["items"][0]["qty"], 10)
            self.assertEqual([e["action"] for e in detail["review_history"]], ["reject", "resubmit"])
            self.assertEqual(detail["review_history"][0]["items"][0]["qty"], 8)
            self.assertEqual(detail["review_history"][1]["items"][0]["qty"], 10)
            self.assertEqual(self.resubmit(rejected).status_code, 409)
            self.assertEqual(self.reject(created).status_code, 409)
            self.assertEqual(self.client.post(f"/api/subcontract/orders/{pending['id']}/audit",json={"revision":created["revision"]}).status_code, 409)
            self.assertEqual(self.client.post(f"/api/subcontract/orders/{pending['id']}/audit",json={"revision":pending["revision"]}).status_code, 200)
            self.assertEqual(self.resubmit(pending).status_code, 409)

    def test_reject_reason_and_resubmit_validation_leave_original_unchanged(self):
        created = data(self.client.post("/api/subcontract/orders", json=self.payload()))
        for reason in ("", "   ", "x" * 1001):
            self.assertEqual(self.reject(created, reason).status_code, 422)
        self.assertEqual(self.resubmit(created).status_code, 409)
        rejected = data(self.reject(created))
        self.assertEqual(self.resubmit(rejected, supplier_id=2).status_code, 400)
        self.assertEqual(self.resubmit(rejected, warehouse_id=2).status_code, 400)
        self.assertEqual(self.resubmit(rejected, items=[{"product_id":2,"qty":10}]).status_code, 400)
        for items in ([], [{"product_id":1,"qty":0}], [{"product_id":1,"qty":-1}]):
            self.assertEqual(self.resubmit(rejected, items=items).status_code, 422)
        detail = data(self.client.get(f"/api/subcontract/orders/{created['id']}"))
        self.assertEqual(detail["status"], "rejected")
        self.assertEqual(detail["items"][0]["qty"], 8)
        self.assertEqual(len(detail["review_history"]), 1)
        body = self.payload(); body.pop("source"); body.pop("direction")
        body.update(revision=rejected["revision"], direction="outbound")
        self.assertEqual(self.client.post(f"/api/subcontract/orders/{created['id']}/resubmit",json=body).status_code,422)

    def test_rejection_resubmission_permissions_and_tenant_isolation(self):
        created = data(self.client.post("/api/subcontract/orders", json=self.payload()))
        self.user.role = "viewer"
        self.assertEqual(self.reject(created).status_code, 403)
        self.assertEqual(self.resubmit(created).status_code, 403)
        self.user.role = "auditor"
        rejected = data(self.reject(created))
        self.assertEqual(self.resubmit(rejected).status_code, 403)
        self.user.role = "operator"
        self.user.tenant_id = 2
        self.assertEqual(self.reject(rejected).status_code, 404)
        self.assertEqual(self.resubmit(rejected).status_code, 404)
        self.user.tenant_id = 1
        another = User(id=3,tenant_id=1,username="other",password_hash="unused",role="operator")
        self.db.add(another); self.db.commit()
        app.dependency_overrides[get_current_user] = lambda: another
        self.assertEqual(self.resubmit(rejected).status_code, 403)
        another.role = "admin"
        self.assertEqual(self.resubmit(rejected).status_code, 200)

    def test_resubmit_preserves_photo_and_multiple_rejections(self):
        record = OcrRecord(tenant_id=1,document_type="subcontract_inbound",image_path="photo.jpg",fields_json={})
        self.db.add(record); self.db.commit()
        created = data(self.client.post(f"/api/subcontract/ocr/{record.id}/commit",json=self.payload()))["order"]
        rejected = data(self.reject(created))
        pending = data(self.resubmit(rejected))
        second = data(self.reject(pending,"第二次核对"))
        self.assertEqual(second["rejection_reason"], "第二次核对")
        self.assertEqual(len(second["review_history"]), 3)
        order = self.db.get(SubcontractOrder,created["id"])
        self.assertEqual(order.source,"ocr")
        self.assertEqual(order.image_path,"photo.jpg")
        self.assertEqual(record.subcontract_order_id,order.id)


if __name__ == "__main__":
    unittest.main()

"""退货运单智能导入的回归测试。"""
import json
from threading import Event, Thread
import time
import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.api import returns as returns_api
from app.main import app
from app.models import Product, ProductSupplier, ReturnImportRecord, Supplier, Tenant, User


def data(response):
    body = response.json()
    return body.get("data", body) if isinstance(body, dict) else body


class ReturnImportTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([Tenant(id=1, name="测试客户"), Tenant(id=2, name="其他客户")])
        self.user = User(id=1, tenant_id=1, username="operator", password_hash="unused", role="operator")
        supplier = Supplier(id=1, tenant_id=1, name="测试供应商", code="SUP-1", return_addr="杭州市测试路 1 号")
        product = Product(id=1, tenant_id=1, sku="SKU-1", name="测试商品")
        self.db.add_all([self.user, supplier, product])
        self.db.flush()
        self.db.add(ProductSupplier(tenant_id=1, product_id=1, supplier_id=1))
        self.db.add(ReturnImportRecord(tenant_id=2, tracking_no="OTHER", sku="SECRET", qty=1))
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def csv(qty="2"):
        return f"运单号,商品编码,数量,商品名称\nYT001,SKU-1,{qty},导入商品名\n".encode("utf-8-sig")

    def test_preview_suggests_mapping_and_commit_upserts(self):
        response = self.client.post("/api/returns/import/preview", files={"file": ("returns.csv", self.csv(), "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        preview = data(response)
        self.assertTrue(preview["required_ready"])
        self.assertEqual(preview["mapping"]["tracking_no"], "运单号")
        mapping = preview["mapping"]

        response = self.client.post("/api/returns/import/commit", files={"file": ("returns.csv", self.csv(), "text/csv")}, data={"mapping": json.dumps(mapping), "operation_id": "test-create-1"})
        self.assertEqual(response.status_code, 200, response.text)
        result = data(response)
        self.assertEqual((result["created"], result["updated"], result["skipped"]), (1, 0, 0))
        record = self.db.query(ReturnImportRecord).filter_by(tenant_id=1, tracking_no="YT001", sku="SKU-1").one()
        self.assertEqual(float(record.qty), 2)
        self.assertEqual(record.product_id, 1)
        self.assertEqual(record.supplier_id, 1)
        self.assertEqual(record.return_addr, "杭州市测试路 1 号")

        response = self.client.post("/api/returns/import/commit", files={"file": ("returns.csv", self.csv("5"), "text/csv")}, data={"mapping": json.dumps(mapping), "operation_id": "test-update-1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((data(response)["created"], data(response)["updated"]), (0, 1))
        self.db.refresh(record)
        self.assertEqual(float(record.qty), 5)

    def test_skips_bad_rows_and_never_exposes_other_tenant(self):
        content = "运单号,SKU,数量\n,SKU-1,2\nYT002,SKU-X,错误\nYT003,SKU-X,3\n".encode("utf-8")
        mapping = {"tracking_no": "运单号", "sku": "SKU", "qty": "数量", "product_name": "", "supplier_code": "", "supplier_name": ""}
        response = self.client.post("/api/returns/import/commit", files={"file": ("x.csv", content, "text/csv")}, data={"mapping": json.dumps(mapping), "operation_id": "test-errors-1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((data(response)["created"], data(response)["skipped"]), (1, 2))
        records = data(self.client.get("/api/returns/records"))
        self.assertEqual([item["tracking_no"] for item in records], ["YT003"])

    def test_rejects_viewer_write_and_invalid_mapping(self):
        self.user.role = "viewer"
        self.assertEqual(self.client.post("/api/returns/import/preview", files={"file": ("x.csv", self.csv(), "text/csv")}).status_code, 403)
        self.user.role = "operator"
        bad_mapping = {"tracking_no": "运单号", "sku": "运单号", "qty": "数量"}
        response = self.client.post("/api/returns/import/commit", files={"file": ("x.csv", self.csv(), "text/csv")}, data={"mapping": json.dumps(bad_mapping), "operation_id": "test-invalid-1"})
        self.assertEqual(response.status_code, 422, response.text)

    def test_cancel_running_import_rolls_back_all_rows(self):
        rows = ["运单号,SKU,数量"] + [f"CANCEL-{index},SKU-1,1" for index in range(100)]
        content = ("\n".join(rows) + "\n").encode("utf-8")
        mapping = {"tracking_no": "运单号", "sku": "SKU", "qty": "数量", "product_name": "", "supplier_code": "", "supplier_name": ""}
        started = Event()
        real_number = returns_api._number

        def slow_number(value):
            started.set()
            time.sleep(0.01)
            return real_number(value)

        result = {}

        def run_import():
            result["response"] = self.client.post(
                "/api/returns/import/commit",
                files={"file": ("cancel.csv", content, "text/csv")},
                data={"mapping": json.dumps(mapping), "operation_id": "cancel-test-1"},
            )

        with patch("app.api.returns._number", side_effect=slow_number):
            worker = Thread(target=run_import)
            worker.start()
            self.assertTrue(started.wait(2), "导入未进入处理循环")
            cancelled = self.client.post("/api/returns/import/cancel", json={"operation_id": "cancel-test-1"})
            worker.join(5)
        self.assertFalse(worker.is_alive(), "取消后导入线程仍未结束")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertTrue(data(cancelled)["cancelled"])
        self.assertEqual(result["response"].status_code, 409, result["response"].text)
        self.assertEqual(self.db.query(ReturnImportRecord).filter(ReturnImportRecord.tenant_id == 1, ReturnImportRecord.tracking_no.like("CANCEL-%")).count(), 0)

    def test_scan_waybill_generates_exact_tenant_table(self):
        self.db.add_all([
            ReturnImportRecord(tenant_id=1, tracking_no="SCAN001", sku="SKU-1", qty=2, product_id=1, product_name="测试商品", supplier_id=1, supplier_name="测试供应商", return_addr="杭州市测试路 1 号"),
            ReturnImportRecord(tenant_id=1, tracking_no="SCAN001", sku="SKU-X", qty=3, product_name="待匹配商品"),
            ReturnImportRecord(tenant_id=2, tracking_no="SCAN001", sku="SECRET-2", qty=99),
        ])
        self.db.commit()
        response = self.client.get("/api/returns/waybill", params={"tracking_no": "SCAN001"})
        self.assertEqual(response.status_code, 200, response.text)
        result = data(response)
        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["total_qty"], 5)
        self.assertFalse(result["complete"])
        self.assertNotIn("SECRET-2", response.text)
        self.assertEqual(self.client.get("/api/returns/waybill", params={"tracking_no": "SCAN001-X"}).status_code, 404)

    def test_vouchers_create_one_sheet_per_supplier(self):
        self.db.add_all([
            ReturnImportRecord(tenant_id=1, tracking_no="G-1", sku="SKU-A", qty=1, supplier_code="SUP-A", supplier_name="甲供应商", return_addr="上海市一号路"),
            ReturnImportRecord(tenant_id=1, tracking_no="G-2", sku="SKU-B", qty=2, supplier_code="SUP-A", supplier_name="甲供应商", return_addr="杭州市二号路"),
            ReturnImportRecord(tenant_id=1, tracking_no="G-3", sku="SKU-B", qty=1, supplier_code="SUP-C", supplier_name="丙供应商", return_addr="苏州市三号路"),
            ReturnImportRecord(tenant_id=1, tracking_no="G-4", sku="SKU-D", qty=1, supplier_code="SUP-D", supplier_name="丁供应商", return_addr="北京市 海淀区 1 号"),
            ReturnImportRecord(tenant_id=1, tracking_no="G-5", sku="SKU-E", qty=1, supplier_code="SUP-E", supplier_name="戊供应商", return_addr="北京市海淀区1号"),
            ReturnImportRecord(tenant_id=2, tracking_no="G-1", sku="TENANT-SECRET", qty=1, supplier_code="SECRET"),
        ])
        self.db.commit()
        response = self.client.post(
            "/api/returns/vouchers/preview",
            json={"tracking_nos": ["G-1", "G-2", "G-3", "G-4", "G-5", "MISSING", "G-1"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = data(response)
        self.assertEqual(result["voucher_count"], 4)
        self.assertEqual(result["sheet_count"], 4)
        self.assertEqual(result["group_rule"], "one_supplier_per_sheet")
        self.assertEqual(result["item_count"], 5)
        self.assertEqual(result["missing_tracking_nos"], ["MISSING"])
        self.assertEqual(result["vouchers"][0]["tracking_nos"], ["G-1", "G-2"])
        self.assertEqual(result["vouchers"][1]["tracking_nos"], ["G-3"])
        self.assertEqual(result["vouchers"][2]["tracking_nos"], ["G-4"])
        self.assertEqual(result["vouchers"][3]["tracking_nos"], ["G-5"])
        self.assertNotIn("TENANT-SECRET", response.text)
        self.assertEqual(result["vouchers"][0]["items"][1]["remark"], "数量：2")

    def test_voucher_export_creates_one_styled_xlsx_sheet_per_supplier(self):
        self.db.add_all([
            ReturnImportRecord(tenant_id=1, tracking_no="XLS-1", sku="SKU-1", qty=1, product_name="葡萄", supplier_code="SUP-1", supplier_name="测试供应商", return_addr="杭州市测试路 1 号"),
            ReturnImportRecord(tenant_id=1, tracking_no="XLS-2", sku="SKU-2", qty=3, product_name="=危险公式", supplier_code="SUP-2", supplier_name="另一个供应商", return_addr="上海市测试路 2 号"),
        ])
        self.db.commit()
        response = self.client.post("/api/returns/vouchers/export", json={"tracking_nos": ["XLS-1", "XLS-2"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])
        with ZipFile(BytesIO(response.content)) as workbook:
            self.assertIn("xl/styles.xml", workbook.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", workbook.namelist())
            self.assertIn("xl/worksheets/sheet2.xml", workbook.namelist())
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            self.assertEqual(workbook_xml.count("<sheet "), 2)
            self.assertIn('name="测试供应商"', workbook_xml)
            self.assertIn('name="另一个供应商"', workbook_xml)
            sheet1 = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            sheet2 = workbook.read("xl/worksheets/sheet2.xml").decode("utf-8")
            self.assertEqual(sheet1.count("退供应商邮件凭证"), 1)
            self.assertEqual(sheet2.count("退供应商邮件凭证"), 1)
            self.assertIn("XLS-1", sheet1)
            self.assertNotIn("XLS-2", sheet1)
            self.assertIn("XLS-2", sheet2)
            self.assertNotIn("XLS-1", sheet2)
            self.assertIn("数量：3", sheet2)
            self.assertIn('<mergeCell ref="A1:E1"/>', sheet1)
            self.assertIn('<mergeCell ref="A1:E1"/>', sheet2)
            self.assertNotIn("<f>", sheet2)

    def test_voucher_export_can_keep_all_suppliers_in_one_landscape_sheet(self):
        self.db.add_all([
            ReturnImportRecord(tenant_id=1, tracking_no="ONE-1", sku="SKU-1", qty=1, product_name="商品甲", supplier_code="SUP-1", supplier_name="甲供应商", return_addr="上海"),
            ReturnImportRecord(tenant_id=1, tracking_no="ONE-2", sku="SKU-2", qty=2, product_name="商品乙", supplier_code="SUP-2", supplier_name="乙供应商", return_addr="北京"),
        ])
        self.db.commit()
        response = self.client.post(
            "/api/returns/vouchers/export",
            json={"tracking_nos": ["ONE-1", "ONE-2"], "layout": "combined_sheet"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("%E5%85%A8%E9%83%A8%E4%BE%9B%E5%BA%94%E5%95%86%E5%8D%95Sheet", response.headers["content-disposition"])
        with ZipFile(BytesIO(response.content)) as workbook:
            sheets = [name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]
            self.assertEqual(sheets, ["xl/worksheets/sheet1.xml"])
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertEqual(sheet.count("退供应商邮件凭证"), 2)
            self.assertIn("ONE-1", sheet)
            self.assertIn("ONE-2", sheet)
            self.assertIn('orientation="landscape"', sheet)
            self.assertIn('<mergeCell ref="A1:E1"/>', sheet)
            self.assertIn('<mergeCell ref="A7:E7"/>', sheet)

    def test_batch_file_import_extracts_deduplicates_matches_and_groups_tracking_numbers(self):
        self.db.add_all([
            ReturnImportRecord(tenant_id=1, tracking_no="BATCH-1", sku="SKU-A", qty=1, supplier_code="SUP-B", supplier_name="批量供应商", return_addr="同一地址"),
            ReturnImportRecord(tenant_id=1, tracking_no="BATCH-2", sku="SKU-B", qty=1, supplier_code="SUP-B", supplier_name="批量供应商", return_addr="同一地址"),
        ])
        self.db.commit()
        before = self.db.query(ReturnImportRecord).filter_by(tenant_id=1).count()
        content = "运单号,备注\nBATCH-1,一\nBATCH-1,重复\nMISSING,无记录\nBATCH-2,二\n".encode("utf-8-sig")
        response = self.client.post("/api/returns/vouchers/import", files={"file": ("batch.csv", content, "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        result = data(response)
        self.assertEqual(result["tracking_column"], "运单号")
        self.assertEqual(result["file_tracking_count"], 3)
        self.assertEqual(result["file_duplicate_count"], 1)
        self.assertEqual(result["file_duplicate_tracking_nos"], ["BATCH-1"])
        self.assertEqual(result["matched_tracking_count"], 2)
        self.assertEqual(result["missing_tracking_nos"], ["MISSING"])
        self.assertEqual(result["voucher_count"], 1)
        self.assertEqual(result["vouchers"][0]["tracking_nos"], ["BATCH-1", "BATCH-2"])
        self.assertEqual(self.db.query(ReturnImportRecord).filter_by(tenant_id=1).count(), before)

    def test_batch_file_import_requires_recognizable_tracking_header(self):
        content = "未知列,备注\nBATCH-1,一\n".encode("utf-8")
        response = self.client.post("/api/returns/vouchers/import", files={"file": ("batch.csv", content, "text/csv")})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("运单号列", response.text)

    def test_reads_first_xlsx_sheet_without_extra_dependency(self):
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            archive.writestr("xl/workbook.xml", '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="导入" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>运单号</t></is></c><c r="B1" t="inlineStr"><is><t>SKU</t></is></c><c r="C1" t="inlineStr"><is><t>数量</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>YT100</t></is></c><c r="B2" t="inlineStr"><is><t>SKU-1</t></is></c><c r="C2"><v>4</v></c></row></sheetData></worksheet>')
        response = self.client.post("/api/returns/import/preview", files={"file": ("returns.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(data(response)["sample"][0]["运单号"], "YT100")

    def test_real_return_schema_uses_product_number_and_defaults_qty_to_one(self):
        content = "运单号,供应商名称,供应商编号,商品编号,商品名称,退货地址\nDEMO-TRACKING-001,演示供应商,DEMO-SUP-001,DEMO-SKU-001,苹果,湖北\n".encode("utf-8")
        response = self.client.post("/api/returns/import/preview", files={"file": ("real.csv", content, "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        preview = data(response)
        self.assertTrue(preview["required_ready"])
        self.assertEqual(preview["mapping"]["sku"], "商品编号")
        self.assertEqual(preview["mapping"]["qty"], "")
        self.assertEqual(preview["mapping"]["return_addr"], "退货地址")
        self.assertEqual(preview["defaults"]["qty"], 1)

        response = self.client.post(
            "/api/returns/import/commit",
            files={"file": ("real.csv", content, "text/csv")},
            data={"mapping": json.dumps(preview["mapping"]), "operation_id": "real-schema-1"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        record = self.db.query(ReturnImportRecord).filter_by(tenant_id=1, tracking_no="DEMO-TRACKING-001").one()
        self.assertEqual(record.sku, "DEMO-SKU-001")
        self.assertEqual(float(record.qty), 1)
        self.assertEqual(record.return_addr, "湖北")


if __name__ == "__main__":
    unittest.main()

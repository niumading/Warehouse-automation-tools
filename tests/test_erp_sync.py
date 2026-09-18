"""旺店通主数据同步回归测试。"""
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models import Product, Supplier, Tenant, TenantSetting, User, Warehouse
from app.services.erp import WdtEnterpriseGateway


def data(response):
    body = response.json()
    return body.get("data", body)


class ErpSyncTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(Tenant(id=1, name="测试客户"))
        self.user = User(id=1, tenant_id=1, username="admin", password_hash="x", role="admin")
        self.db.add(self.user)
        self.db.add(TenantSetting(
            tenant_id=1, erp_type="wdt_qyb",
            erp_base_url="https://sandbox.wangdian.cn/openapi2",
            erp_sid="seller", erp_appkey="key", erp_appsecret="secret",
        ))
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_sync_upserts_without_deleting_local_master_data(self):
        gateway = WdtEnterpriseGateway(
            "https://sandbox.wangdian.cn/openapi2", "seller", "key", "secret"
        )
        gateway.fetch_master_data = Mock(return_value={
            "suppliers": [{
                "provider_no": "SUP-1", "provider_name": "供应商甲", "contact": "张三",
                "mobile": "13800000000", "address": "测试地址", "is_disabled": "0", "deleted": "0",
            }],
            "warehouses": [{"warehouse_no": "WH-1", "name": "主仓", "is_disabled": "0"}],
            "goods": [{
                "goods_no": "GOOD-1", "goods_name": "河虾仁", "unit_name": "箱",
                "spec_list": [{"spec_no": "SKU-1", "spec_name": "20g", "spec_unit_name": "盒", "deleted": "0"}],
            }],
        })
        with patch("app.api.sync.get_erp_gateway", return_value=gateway):
            first = self.client.post("/api/sync/erp")
            second = self.client.post("/api/sync/erp")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(data(first)["suppliers"]["created"], 1)
        self.assertEqual(data(second)["suppliers"]["updated"], 1)
        self.assertEqual(self.db.query(Supplier).count(), 1)
        self.assertEqual(self.db.query(Warehouse).count(), 1)
        self.assertEqual(self.db.query(Product).count(), 1)
        self.assertEqual(self.db.query(Product).first().unit, "盒")

    def test_sync_deduplicates_repeated_remote_codes_in_one_batch(self):
        gateway = WdtEnterpriseGateway(
            "https://sandbox.wangdian.cn/openapi2", "seller", "key", "secret"
        )
        supplier = {
            "provider_no": "SUP-DUP", "provider_name": "重复供应商",
            "is_disabled": "0", "deleted": "0",
        }
        warehouse = {"warehouse_no": "WH-DUP", "name": "重复仓库", "is_disabled": "0"}
        goods = {
            "goods_no": "GOOD-DUP", "goods_name": "重复商品", "unit_name": "箱",
            "spec_list": [
                {"spec_no": "SKU-DUP", "spec_name": "红色", "spec_unit_name": "件", "deleted": "0"},
                {"spec_no": "SKU-DUP", "spec_name": "红色重复", "spec_unit_name": "件", "deleted": "0"},
            ],
        }
        gateway.fetch_master_data = Mock(return_value={
            "suppliers": [supplier, dict(supplier)],
            "warehouses": [warehouse, dict(warehouse)],
            "goods": [goods, dict(goods)],
        })
        with patch("app.api.sync.get_erp_gateway", return_value=gateway):
            response = self.client.post("/api/sync/erp")
        self.assertEqual(response.status_code, 200, response.text)
        result = data(response)
        self.assertEqual(result["suppliers"], {"created": 1, "updated": 0, "skipped": 1})
        self.assertEqual(result["warehouses"], {"created": 1, "updated": 0, "skipped": 1})
        self.assertEqual(result["products"], {"created": 1, "updated": 0, "skipped": 3})
        self.assertEqual(self.db.query(Supplier).filter_by(code="SUP-DUP").count(), 1)
        self.assertEqual(self.db.query(Warehouse).filter_by(code="WH-DUP").count(), 1)
        self.assertEqual(self.db.query(Product).filter_by(sku="SKU-DUP").count(), 1)


if __name__ == "__main__":
    unittest.main()

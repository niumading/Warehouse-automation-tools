"""AI 识别工作流中不依赖外部模型的单元测试。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.ai_config import AiConfig
from app.ai.workflow import (
    OutboundVisionDocument, VisionDocument, _best_match, _build_match_node,
    _build_outbound_match_node, _build_outbound_vision_node, _build_vision_node,
    _extract_json, _normalize_outbound_type, _similarity,
)
from app.models import Product, ProductSupplier, Supplier, Warehouse


class QueryStub:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return self.rows


class DbStub:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model

    def query(self, model):
        return QueryStub(self.rows_by_model.get(model, []))


class WorkflowHelpersTest(unittest.TestCase):
    def test_normalizes_chinese_supplier_name(self):
        self.assertEqual(_similarity("演示 供应商甲", "演示供应商甲"), 1.0)

    def test_best_match_does_not_invent_unknown_product(self):
        products = [SimpleNamespace(name="演示商品甲", sku="SKU-10231", code=None, spec=None)]
        matched, score = _best_match("完全无关商品", products, ["name", "sku"])
        self.assertIsNone(matched)
        self.assertLess(score, 0.58)

    def test_exact_product_can_match_when_supplier_links_are_incomplete(self):
        supplier = SimpleNamespace(id=1, tenant_id=1, name="演示供应商甲", code=None)
        linked = SimpleNamespace(product_id=1)
        products = [
            SimpleNamespace(id=1, name="演示商品甲", sku="SKU-1", code=None, spec="20g", unit="盒"),
            SimpleNamespace(id=2, name="演示商品乙", sku="SKU-2", code=None, spec="20g", unit="盒"),
        ]
        db = DbStub({Supplier: [supplier], Warehouse: [], Product: products, ProductSupplier: [linked]})
        document = VisionDocument.model_validate({
            "supplier_name": "演示供应商甲有限公司",
            "supplier_confidence": 0.95,
            "items": [{"name": "演示商品乙", "qty": 600, "unit": "盒", "confidence": 0.9}],
        })
        result = _build_match_node(db)({"tenant_id": 1, "extracted": document})["recognized"]
        self.assertEqual(result["items"][0]["product_id"], 2)
        self.assertEqual(result["items"][0]["match_score"], 1.0)

    def test_accepts_json_code_fence_and_percentage_confidence(self):
        payload = _extract_json(
            '```json\n{"supplier_name":"演示供应商甲","supplier_confidence":96,'
            '"items":[],"raw_text":"送货单"}\n```'
        )
        doc = VisionDocument.model_validate(payload)
        self.assertEqual(doc.supplier_name, "演示供应商甲")
        self.assertAlmostEqual(doc.supplier_confidence, 0.96)

    def test_vision_request_has_bounded_wait_and_no_automatic_retry(self):
        fake_settings = SimpleNamespace(
            llm_temperature=0.1,
            llm_request_timeout_seconds=60,
            llm_max_retries=0,
        )
        response = SimpleNamespace(content='{"items": [], "raw_text": ""}')
        with patch("app.ai.workflow.get_settings", return_value=fake_settings), patch(
            "app.ai.workflow.get_ai_config",
            return_value=AiConfig("https://api.example.com/v1", "vision", "secret"),
        ), patch("app.ai.workflow.Path.read_bytes", return_value=b"image"), patch(
            "app.ai.workflow.ChatOpenAI"
        ) as llm:
            llm.return_value.invoke.return_value = response
            _build_vision_node()({"image_path": "test.jpg", "tenant_id": 1})
            self.assertEqual(llm.call_args.kwargs["request_timeout"], 60)
            self.assertEqual(llm.call_args.kwargs["max_retries"], 0)

    def test_vision_prompt_handles_rotated_forms_and_uses_master_candidates(self):
        fake_settings = SimpleNamespace(
            llm_temperature=0.1,
            llm_request_timeout_seconds=60,
            llm_max_retries=0,
        )
        response = SimpleNamespace(content='{"items": [], "raw_text": ""}')
        with patch("app.ai.workflow.get_settings", return_value=fake_settings), patch(
            "app.ai.workflow.get_ai_config",
            return_value=AiConfig("https://api.example.com/v1", "vision", "secret"),
        ), patch("app.ai.workflow.Path.read_bytes", return_value=b"image"), patch(
            "app.ai.workflow.ChatOpenAI"
        ) as llm:
            llm.return_value.invoke.return_value = response
            _build_vision_node()({
                "image_path": "test.jpg",
                "tenant_id": 1,
                "reference_data": "供应商候选：演示供应商甲；商品候选：演示商品丙",
            })
            message = llm.return_value.invoke.call_args.args[0][0]
            prompt = message.content[0]["text"]
            self.assertIn("0°、90°、180°、270°", prompt)
            self.assertIn("演示供应商甲", prompt)
            self.assertIn("演示商品丙", prompt)

    def test_outbound_matches_warehouse_product_and_normalizes_type(self):
        warehouses = [
            SimpleNamespace(id=1, name="主仓", code="WH1"),
            SimpleNamespace(id=2, name="门店仓", code="WH2"),
        ]
        products = [
            SimpleNamespace(id=1, name="工作服", sku="SKU-1", code=None, spec="XL", unit="件")
        ]
        db = DbStub({Warehouse: warehouses, Product: products})
        document = OutboundVisionDocument.model_validate({
            "outbound_type": "调仓单",
            "warehouse_name": "主仓",
            "destination_warehouse_name": "门店仓",
            "warehouse_confidence": 0.99,
            "document_confidence": 0.98,
            "items": [{"name": "工作服", "sku": "SKU-1", "qty": 4, "confidence": 0.99}],
        })
        result = _build_outbound_match_node(db)({"tenant_id": 1, "extracted": document})["recognized"]
        self.assertEqual(_normalize_outbound_type("领用单"), "requisition")
        self.assertEqual(_normalize_outbound_type("报损"), "scrap")
        self.assertEqual(result["outbound_type"], "transfer")
        self.assertEqual(result["warehouse"]["warehouse_id"], 1)
        self.assertEqual(result["destination_warehouse"]["warehouse_id"], 2)
        self.assertEqual(result["items"][0]["product_id"], 1)
        self.assertTrue(result["ready_for_creation"])

    def test_outbound_vision_prompt_covers_rotation_and_master_candidates(self):
        fake_settings = SimpleNamespace(
            llm_temperature=0.1,
            llm_request_timeout_seconds=60,
            llm_max_retries=0,
        )
        response = SimpleNamespace(content='{"items": [], "raw_text": ""}')
        with patch("app.ai.workflow.get_settings", return_value=fake_settings), patch(
            "app.ai.workflow.get_ai_config",
            return_value=AiConfig("https://api.example.com/v1", "vision", "secret"),
        ), patch("app.ai.workflow.Path.read_bytes", return_value=b"image"), patch(
            "app.ai.workflow.ChatOpenAI"
        ) as llm:
            llm.return_value.invoke.return_value = response
            _build_outbound_vision_node()({
                "image_path": "test.jpg",
                "tenant_id": 1,
                "reference_data": "仓库候选：主仓；商品候选：工作服",
            })
            message = llm.return_value.invoke.call_args.args[0][0]
            prompt = message.content[0]["text"]
            self.assertIn("其他出库单识别器", prompt)
            self.assertIn("0°、90°、180°、270°", prompt)
            self.assertIn("主仓", prompt)
            self.assertIn("工作服", prompt)


if __name__ == "__main__":
    unittest.main()

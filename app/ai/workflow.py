"""采购单视觉识别与主数据匹配工作流。"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.ai_config import get_ai_config
from app.core.config import get_settings
from app.models import Product, ProductSupplier, Supplier, Warehouse


class VisionItem(BaseModel):
    name: str = ""
    sku: str | None = None
    spec: str | None = None
    qty: float | None = None
    unit: str | None = None
    price: float | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> float:
        number = float(value or 0)
        return number / 100 if number > 1 else number


class VisionDocument(BaseModel):
    supplier_name: str | None = None
    warehouse_name: str | None = None
    document_date: str | None = None
    raw_text: str = ""
    supplier_confidence: float = Field(default=0.0, ge=0, le=1)
    warehouse_confidence: float = Field(default=0.0, ge=0, le=1)
    date_confidence: float = Field(default=0.0, ge=0, le=1)
    items: list[VisionItem] = Field(default_factory=list)

    @field_validator("supplier_confidence", "warehouse_confidence", "date_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> float:
        number = float(value or 0)
        return number / 100 if number > 1 else number


class OutboundVisionDocument(BaseModel):
    outbound_type: str | None = None
    warehouse_name: str | None = None
    destination_warehouse_name: str | None = None
    department: str | None = None
    recipient: str | None = None
    remark: str | None = None
    document_date: str | None = None
    raw_text: str = ""
    warehouse_confidence: float = Field(default=0.0, ge=0, le=1)
    document_confidence: float = Field(default=0.0, ge=0, le=1)
    items: list[VisionItem] = Field(default_factory=list)

    @field_validator("warehouse_confidence", "document_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> float:
        number = float(value or 0)
        return number / 100 if number > 1 else number


class OcrState(TypedDict, total=False):
    image_path: str
    tenant_id: int
    reference_data: str
    extracted: Any
    recognized: dict[str, Any]


PROMPT = """你是采购入库单识别器。识别前先判断纸张文字方向；图片可能横置或倒置，必须分别检查 0°、90°、180°、270°，按文字正向阅读。
只识别画面最前方的主单据，不要把背景纸张的字段混入结果。供应商通常是单据顶部印刷的公司全称，不要用客户名称、印章文字或收货单位代替。
逐行沿表格列读取商品名称、规格、单位、数量、单价；数量列与单价、金额列必须分开，禁止把金额当数量。
手写内容要逐字如实读取；看不清就留空，禁止猜测或补造。数量只输出数字。
下方可能提供当前客户的供应商和商品候选。候选仅用于校正看得见且笔画相符的名称，无法确认时仍须保留原文或留空，禁止强行套用。
只返回一个 JSON 对象，不要 Markdown，不要解释，结构必须是：
{
  "supplier_name": null,
  "warehouse_name": null,
  "document_date": null,
  "raw_text": "图片中可辨认的原文",
  "supplier_confidence": 0.0,
  "warehouse_confidence": 0.0,
  "date_confidence": 0.0,
  "items": [
    {"name":"", "sku":null, "spec":null, "qty":null, "unit":null,
     "price":null, "confidence":0.0}
  ]
}
所有 confidence 使用 0 到 1 的小数。不要把合计、备注或表头当成商品行。"""


OUTBOUND_PROMPT = """你是其他出库单识别器。识别前先判断纸张文字方向；图片可能横置或倒置，必须分别检查 0°、90°、180°、270°，按文字正向阅读。
只识别画面最前方的主单据，不要混入背景纸张。判断业务类型属于领用出库、报废出库、调拨出库或其他出库；无法确认时输出“其他”。
识别出库仓库、调入仓库（仅调拨时）、领用部门、领用人/经手人、备注和日期。逐行读取商品名称、SKU/货号、规格、单位和出库数量。
数量列与单价、金额列必须分开，禁止把金额当数量；签字、审批人、合计、备注或表头不能作为商品行。
手写内容要逐字如实读取；看不清就留空，禁止猜测或补造。数量只输出数字。
下方可能提供当前客户的仓库和商品候选。候选仅用于校正看得见且笔画相符的名称，无法确认时保留原文或留空，禁止强行套用。
只返回一个 JSON 对象，不要 Markdown，不要解释，结构必须是：
{
  "outbound_type": "领用|报废|调拨|其他",
  "warehouse_name": null,
  "destination_warehouse_name": null,
  "department": null,
  "recipient": null,
  "remark": null,
  "document_date": null,
  "raw_text": "图片中可辨认的原文",
  "warehouse_confidence": 0.0,
  "document_confidence": 0.0,
  "items": [
    {"name":"", "sku":null, "spec":null, "qty":null, "unit":null,
     "price":null, "confidence":0.0}
  ]
}
所有 confidence 使用 0 到 1 的小数。"""


def _normalized(value: str | None) -> str:
    if not value:
        return ""
    compact = re.sub(r"[\s\-_—（）()【】\[\]·.,，。/]", "", value).lower()
    return re.sub(r"(?:股份有限公司|有限责任公司|有限公司|公司)$", "", compact)


def _reference_data(db: Session, tenant_id: int) -> str:
    suppliers = db.query(Supplier).filter(Supplier.tenant_id == tenant_id).limit(100).all()
    products = db.query(Product).filter(Product.tenant_id == tenant_id).limit(200).all()
    payload = {
        "供应商候选": [row.name for row in suppliers],
        "商品候选": [
            {"名称": row.name, "SKU": row.sku, "规格": row.spec}
            for row in products
        ],
    }
    return "\n当前客户主数据候选（只作视觉校正参考）：\n" + json.dumps(payload, ensure_ascii=False)


def _outbound_reference_data(db: Session, tenant_id: int) -> str:
    warehouses = db.query(Warehouse).filter(Warehouse.tenant_id == tenant_id).limit(100).all()
    products = db.query(Product).filter(Product.tenant_id == tenant_id).limit(300).all()
    payload = {
        "仓库候选": [{"名称": row.name, "编码": row.code} for row in warehouses],
        "商品候选": [
            {"名称": row.name, "SKU": row.sku, "规格": row.spec}
            for row in products
        ],
    }
    return "\n当前客户主数据候选（只作视觉校正参考）：\n" + json.dumps(payload, ensure_ascii=False)


def _similarity(left: str | None, right: str | None) -> float:
    a, b = _normalized(left), _normalized(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def _best_match(query: str | None, rows: list[Any], values: list[str], threshold: float = 0.58):
    best, best_score = None, 0.0
    for row in rows:
        score = max((_similarity(query, getattr(row, field, None)) for field in values), default=0.0)
        if score > best_score:
            best, best_score = row, score
    return (best if best_score >= threshold else None), round(best_score, 4)


def _extract_json(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回有效 JSON")
    return json.loads(text[start : end + 1])


def _build_vision_node():
    settings = get_settings()
    ai_config = get_ai_config()

    def vision(state: OcrState) -> dict[str, Any]:
        image_path = Path(state["image_path"])
        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        model_args: dict[str, Any] = {
            "model": ai_config.vision_model,
            "api_key": ai_config.api_key,
            "temperature": settings.llm_temperature,
            "request_timeout": settings.llm_request_timeout_seconds,
            "max_retries": settings.llm_max_retries,
        }
        if ai_config.base_url:
            model_args["base_url"] = ai_config.base_url
        llm = ChatOpenAI(**model_args)
        message = HumanMessage(content=[
            {"type": "text", "text": PROMPT + state.get("reference_data", "")},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ])
        response = llm.invoke([message])
        return {"extracted": VisionDocument.model_validate(_extract_json(response.content))}

    return vision


def _build_outbound_vision_node():
    settings = get_settings()
    ai_config = get_ai_config()

    def vision(state: OcrState) -> dict[str, Any]:
        image_path = Path(state["image_path"])
        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        model_args: dict[str, Any] = {
            "model": ai_config.vision_model,
            "api_key": ai_config.api_key,
            "temperature": settings.llm_temperature,
            "request_timeout": settings.llm_request_timeout_seconds,
            "max_retries": settings.llm_max_retries,
        }
        if ai_config.base_url:
            model_args["base_url"] = ai_config.base_url
        llm = ChatOpenAI(**model_args)
        message = HumanMessage(content=[
            {"type": "text", "text": OUTBOUND_PROMPT + state.get("reference_data", "")},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ])
        response = llm.invoke([message])
        return {"extracted": OutboundVisionDocument.model_validate(_extract_json(response.content))}

    return vision


def _normalize_outbound_type(value: str | None) -> str:
    text = _normalized(value)
    if "领用" in text:
        return "requisition"
    if "报废" in text or "报损" in text or "损耗" in text:
        return "scrap"
    if "调拨" in text or "调仓" in text:
        return "transfer"
    return "other"


def _build_outbound_match_node(db: Session):
    def match(state: OcrState) -> dict[str, Any]:
        tenant_id = state["tenant_id"]
        doc: OutboundVisionDocument = state["extracted"]
        warehouses = db.query(Warehouse).filter(Warehouse.tenant_id == tenant_id).all()
        products = db.query(Product).filter(Product.tenant_id == tenant_id).all()
        warehouse, warehouse_score = _best_match(doc.warehouse_name, warehouses, ["name", "code"])
        destination, destination_score = _best_match(
            doc.destination_warehouse_name, warehouses, ["name", "code"]
        )
        matched_items: list[dict[str, Any]] = []
        confidence_values: list[float] = []
        low_fields: list[str] = []
        for index, item in enumerate(doc.items):
            product, match_score = _best_match(
                item.sku or item.name,
                products,
                ["sku"] if item.sku else ["name", "code", "spec"],
                threshold=0.56,
            )
            field_confidence = round(min(item.confidence, match_score) if product else item.confidence, 4)
            if product is None or field_confidence < 0.95:
                low_fields.append(f"items.{index}")
            if item.qty is None or item.qty <= 0:
                low_fields.append(f"items.{index}.qty")
            confidence_values.append(field_confidence)
            matched_items.append({
                "raw_name": item.name,
                "raw_sku": item.sku,
                "product_id": product.id if product else None,
                "product_name": product.name if product else item.name,
                "sku": product.sku if product else item.sku,
                "qty": item.qty,
                "unit": product.unit if product and product.unit else item.unit,
                "confidence": field_confidence,
                "match_score": match_score,
                "needs_review": product is None or field_confidence < 0.95 or not item.qty or item.qty <= 0,
            })
        if doc.warehouse_name:
            confidence_values.append(min(doc.warehouse_confidence, warehouse_score) if warehouse else doc.warehouse_confidence)
        if warehouse is None:
            low_fields.append("warehouse")
        outbound_type = _normalize_outbound_type(doc.outbound_type)
        if outbound_type == "transfer" and destination is None:
            low_fields.append("destination_warehouse")
        overall = round(min(confidence_values), 4) if confidence_values else 0.0
        ready = bool(warehouse and matched_items) and all(
            item["product_id"] and item["qty"] and item["qty"] > 0 for item in matched_items
        )
        if outbound_type == "transfer":
            ready = ready and destination is not None and destination.id != warehouse.id
        return {"recognized": {
            "outbound_type": outbound_type,
            "warehouse": {
                "raw_name": doc.warehouse_name,
                "warehouse_id": warehouse.id if warehouse else None,
                "name": warehouse.name if warehouse else doc.warehouse_name,
                "match_score": warehouse_score,
                "needs_review": warehouse is None or min(doc.warehouse_confidence, warehouse_score) < 0.95,
            },
            "destination_warehouse": {
                "raw_name": doc.destination_warehouse_name,
                "warehouse_id": destination.id if destination else None,
                "name": destination.name if destination else doc.destination_warehouse_name,
                "match_score": destination_score,
                "needs_review": outbound_type == "transfer" and destination is None,
            },
            "department": doc.department,
            "recipient": doc.recipient,
            "remark": doc.remark,
            "document_date": doc.document_date,
            "items": matched_items,
            "overall_confidence": overall,
            "low_confidence_fields": sorted(set(low_fields)),
            "ready_for_creation": ready,
        }}

    return match


def _build_match_node(db: Session):
    def match(state: OcrState) -> dict[str, Any]:
        tenant_id = state["tenant_id"]
        doc = state["extracted"]
        suppliers = db.query(Supplier).filter(Supplier.tenant_id == tenant_id).all()
        warehouses = db.query(Warehouse).filter(Warehouse.tenant_id == tenant_id).all()
        products = db.query(Product).filter(Product.tenant_id == tenant_id).all()

        supplier, supplier_score = _best_match(doc.supplier_name, suppliers, ["name", "code"])
        warehouse, warehouse_score = _best_match(doc.warehouse_name, warehouses, ["name", "code"])

        supplier_linked_ids: set[int] = set()
        if supplier is not None:
            supplier_linked_ids = {
                row.product_id for row in db.query(ProductSupplier).filter(
                    ProductSupplier.tenant_id == tenant_id,
                    ProductSupplier.supplier_id == supplier.id,
                ).all()
            }

        matched_items: list[dict[str, Any]] = []
        confidence_values: list[float] = []
        low_fields: list[str] = []
        for index, item in enumerate(doc.items):
            product, match_score = _best_match(
                item.sku or item.name,
                products,
                ["sku"] if item.sku else ["name", "code", "spec"],
                threshold=0.56,
            )
            if product is not None and product.id in supplier_linked_ids and match_score < 1:
                match_score = round(min(1.0, match_score + 0.05), 4)
            field_confidence = round(min(item.confidence, match_score) if product else item.confidence, 4)
            if product is None or field_confidence < 0.95:
                low_fields.append(f"items.{index}")
            if item.qty is None or item.qty <= 0:
                low_fields.append(f"items.{index}.qty")
            confidence_values.append(field_confidence)
            matched_items.append({
                "raw_name": item.name,
                "raw_sku": item.sku,
                "spec": item.spec,
                "product_id": product.id if product else None,
                "product_name": product.name if product else item.name,
                "sku": product.sku if product else item.sku,
                "qty": item.qty,
                "unit": product.unit if product and product.unit else item.unit,
                "price": item.price,
                "confidence": field_confidence,
                "match_score": match_score,
                "needs_review": product is None or field_confidence < 0.95 or not item.qty or item.qty <= 0,
            })

        if doc.supplier_name:
            confidence_values.append(min(doc.supplier_confidence, supplier_score) if supplier else doc.supplier_confidence)
        if supplier is None:
            low_fields.append("supplier")
        overall = round(min(confidence_values), 4) if confidence_values else 0.0
        ready = bool(supplier and matched_items) and all(
            item["product_id"] and item["qty"] and item["qty"] > 0 for item in matched_items
        )
        return {"recognized": {
            "supplier": {
                "raw_name": doc.supplier_name,
                "supplier_id": supplier.id if supplier else None,
                "name": supplier.name if supplier else doc.supplier_name,
                "confidence": doc.supplier_confidence,
                "match_score": supplier_score,
                "needs_review": supplier is None or min(doc.supplier_confidence, supplier_score) < 0.95,
            },
            "warehouse": {
                "raw_name": doc.warehouse_name,
                "warehouse_id": warehouse.id if warehouse else None,
                "name": warehouse.name if warehouse else doc.warehouse_name,
                "confidence": doc.warehouse_confidence,
                "match_score": warehouse_score,
                "needs_review": bool(doc.warehouse_name) and warehouse is None,
            },
            "document_date": doc.document_date,
            "date_confidence": doc.date_confidence,
            "items": matched_items,
            "overall_confidence": overall,
            "low_confidence_fields": sorted(set(low_fields)),
            "ready_for_auto_audit": ready,
        }}

    return match


def recognize_purchase_image(db: Session, tenant_id: int, image_path: str) -> tuple[dict[str, Any], str]:
    """执行 视觉识别 → 主数据匹配，返回可编辑草稿与原始文本。"""
    builder = StateGraph(OcrState)
    builder.add_node("vision", _build_vision_node())
    builder.add_node("match", _build_match_node(db))
    builder.add_edge(START, "vision")
    builder.add_edge("vision", "match")
    builder.add_edge("match", END)
    result = builder.compile().invoke({
        "tenant_id": tenant_id,
        "image_path": image_path,
        "reference_data": _reference_data(db, tenant_id),
    })
    document: VisionDocument = result["extracted"]
    return result["recognized"], document.raw_text


def recognize_outbound_image(db: Session, tenant_id: int, image_path: str) -> tuple[dict[str, Any], str]:
    """执行其他出库单视觉识别与商品、仓库主数据匹配。"""
    builder = StateGraph(OcrState)
    builder.add_node("vision", _build_outbound_vision_node())
    builder.add_node("match", _build_outbound_match_node(db))
    builder.add_edge(START, "vision")
    builder.add_edge("vision", "match")
    builder.add_edge("match", END)
    result = builder.compile().invoke({
        "tenant_id": tenant_id,
        "image_path": image_path,
        "reference_data": _outbound_reference_data(db, tenant_id),
    })
    document: OutboundVisionDocument = result["extracted"]
    return result["recognized"], document.raw_text

"""OCR 路由：上传图片 → 识别草稿 → 人工确认/自动审核。"""
from datetime import datetime, timezone
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.workflow import recognize_purchase_image
from app.core.ai_config import get_ai_config
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db, set_tenant_context
from app.core.deps import require_role, ROLE_ADMIN, ROLE_OPERATOR
from app.models import (
    OcrRecord, Product, PurchaseOrder, PurchaseOrderItem, Supplier,
    TenantSetting, User, Warehouse,
)
from app.schemas import OrderItemCreate

router = APIRouter()
settings = get_settings()


class OcrCommitRequest(BaseModel):
    supplier_id: int | None = None
    warehouse_id: int | None = None
    items: list[OrderItemCreate] = Field(min_length=1)


def _generate_order_no(db: Session, tenant_id: int) -> str:
    seq = db.execute(text("SELECT nextval('purchase_order_no_seq')")).scalar()
    return f"{tenant_id:04d}{datetime.now().strftime('%Y%m%d')}{seq:06d}"


def _configured() -> bool:
    return get_ai_config().configured


def _recognition_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "model_capability_not_supported" in text or "does not support image" in text:
        return "当前模型不支持图片识别，请在设置中选择视觉模型。"
    if "model_not_available" in text or "model not found" in text:
        return "当前接口没有这个模型，请核对 API 地址和视觉模型名称。"
    if "401" in text or "unauthorized" in text or "invalid api key" in text:
        return "API Key 无效或已失效，请在设置中重新填写。"
    if "429" in text or "rate limit" in text or "insufficient" in text:
        return "模型接口限流或余额不足，请稍后重试并检查账户余额。"
    if "503" in text or "unavailable" in text:
        return "模型服务暂不可用（503），本次识别已停止，请稍后再试。"
    if "504" in text or "timeout" in text or "timed out" in text or "gateway" in text:
        return "模型服务响应超时，本次识别已停止，请稍后再试。"
    return "图片已保存，但模型调用失败。请检查 API 配置后重试。"


def _recognize_record(record_id: int, tenant_id: int, image_path: str) -> None:
    """后台执行模型识别，避免浏览器与代理长连接超时。"""
    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        record = db.query(OcrRecord).filter(
            OcrRecord.id == record_id,
            OcrRecord.tenant_id == tenant_id,
        ).first()
        if record is None:
            return
        try:
            recognized, raw_text = recognize_purchase_image(db, tenant_id, image_path)
            record.raw_text = raw_text
            record.confidence = recognized["overall_confidence"]
            record.fields_json = {"status": "recognized", "recognized": recognized}
        except Exception as exc:
            db.rollback()
            record = db.query(OcrRecord).filter(
                OcrRecord.id == record_id,
                OcrRecord.tenant_id == tenant_id,
            ).first()
            if record is None:
                return
            record.fields_json = {
                "status": "recognition_failed",
                "error": str(exc)[:500],
                "message": _recognition_error_message(exc),
            }
        db.commit()
    finally:
        db.close()


@router.post("/upload")
def upload_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    """上传采购单图片，保存到本地磁盘，记录 ocr_records。"""
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="不支持的图片格式")

    content = file.file.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过大小限制")

    tenant_dir = Path(settings.upload_dir) / str(user.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = tenant_dir / filename
    file_path.write_bytes(content)

    record = OcrRecord(
        tenant_id=user.tenant_id,
        image_path=str(file_path),
        fields_json={"status": "processing"} if _configured() else {"status": "needs_configuration"},
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    result = {
        "ocr_record_id": record.id,
        "image_path": str(file_path),
        "recognized": None,
        "action": "needs_configuration",
    }
    if not _configured():
        result["message"] = "图片已保存；请先在 .env 配置多模态 API 后重新上传"
        return result

    background_tasks.add_task(_recognize_record, record.id, user.tenant_id, str(file_path))
    result.update(action="processing", message="图片已保存，正在后台识别")
    return result


@router.get("/{record_id}")
def get_recognition(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    record = db.query(OcrRecord).filter(
        OcrRecord.id == record_id,
        OcrRecord.tenant_id == user.tenant_id,
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="识别记录不存在")
    fields = record.fields_json or {}
    action = fields.get("status") or ("recognized" if fields.get("recognized") else "processing")
    return {
        "ocr_record_id": record.id,
        "image_path": record.image_path,
        "recognized": fields.get("recognized"),
        "action": action,
        "message": fields.get("message") or (
            "识别完成，请核对后生成单据" if action == "recognized" else "正在后台识别"
        ),
    }


@router.post("/{record_id}/commit")
def commit_draft(
    record_id: int,
    req: OcrCommitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    """保存核对后的识别草稿，幂等生成采购入库单。"""
    record = db.query(OcrRecord).filter(
        OcrRecord.id == record_id,
        OcrRecord.tenant_id == user.tenant_id,
    ).with_for_update().first()
    if record is None:
        raise HTTPException(status_code=404, detail="识别记录不存在")
    if record.order_id:
        order = db.query(PurchaseOrder).filter(
            PurchaseOrder.id == record.order_id,
            PurchaseOrder.tenant_id == user.tenant_id,
        ).first()
        return {"order": _order_dict(order), "auto_audited": order.status == "audited", "reused": True}

    if req.supplier_id is not None and not db.query(Supplier).filter(
        Supplier.id == req.supplier_id, Supplier.tenant_id == user.tenant_id
    ).first():
        raise HTTPException(status_code=400, detail="供应商不属于当前客户")
    if req.warehouse_id is not None and not db.query(Warehouse).filter(
        Warehouse.id == req.warehouse_id, Warehouse.tenant_id == user.tenant_id
    ).first():
        raise HTTPException(status_code=400, detail="仓库不属于当前客户")

    checked_items: list[OrderItemCreate] = []
    for index, item in enumerate(req.items):
        if item.qty is None or item.qty <= 0:
            raise HTTPException(status_code=400, detail=f"第 {index + 1} 行数量需大于 0")
        if item.product_id is not None:
            product = db.query(Product).filter(
                Product.id == item.product_id, Product.tenant_id == user.tenant_id
            ).first()
            if product is None:
                raise HTTPException(status_code=400, detail=f"第 {index + 1} 行商品不属于当前客户")
        checked_items.append(item)

    recognized = (record.fields_json or {}).get("recognized") or {}
    original = {
        "supplier_id": (recognized.get("supplier") or {}).get("supplier_id"),
        "warehouse_id": (recognized.get("warehouse") or {}).get("warehouse_id"),
        "items": [
            {"product_id": row.get("product_id"), "qty": row.get("qty")}
            for row in recognized.get("items", [])
        ],
    }
    submitted = {
        "supplier_id": req.supplier_id,
        "warehouse_id": req.warehouse_id,
        "items": [{"product_id": row.product_id, "qty": row.qty} for row in checked_items],
    }
    corrected = original != submitted

    tenant_setting = db.get(TenantSetting, user.tenant_id)
    threshold = float(tenant_setting.audit_threshold) if tenant_setting else 0.95
    auto_enabled = bool(tenant_setting and tenant_setting.auto_audit_enabled)
    confidence = float(record.confidence or 0)
    ready = bool(recognized.get("ready_for_auto_audit"))
    auto_audited = auto_enabled and ready and not corrected and confidence >= threshold

    order = PurchaseOrder(
        tenant_id=user.tenant_id,
        order_no=_generate_order_no(db, user.tenant_id),
        status="audited" if auto_audited else "pending_audit",
        source="ocr",
        supplier_id=req.supplier_id,
        warehouse_id=req.warehouse_id,
        image_path=record.image_path,
        created_by=user.id,
        audited_at=datetime.now(timezone.utc) if auto_audited else None,
    )
    db.add(order)
    db.flush()
    for item in checked_items:
        db.add(PurchaseOrderItem(
            tenant_id=user.tenant_id,
            order_id=order.id,
            **item.model_dump(),
        ))
    record.order_id = order.id
    record.fields_json = {
        **(record.fields_json or {}),
        "submitted": submitted,
        "correction_diff": {"changed": corrected, "original": original, "submitted": submitted},
    }
    db.commit()
    db.refresh(order)
    return {"order": _order_dict(order), "auto_audited": auto_audited, "reused": False}


def _order_dict(order: PurchaseOrder) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "source": order.source,
        "supplier_id": order.supplier_id,
        "warehouse_id": order.warehouse_id,
        "erp_order_id": order.erp_order_id,
        "created_at": order.created_at,
    }

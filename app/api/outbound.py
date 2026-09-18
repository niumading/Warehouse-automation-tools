"""其他出库：拍照识别、人工核对并生成待审核出库单。"""
from datetime import datetime
from pathlib import Path
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.workflow import recognize_outbound_image
from app.api.ocr import _recognition_error_message
from app.core.ai_config import get_ai_config
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db, set_tenant_context
from app.core.deps import get_current_user, require_role, ROLE_ADMIN, ROLE_OPERATOR
from app.models import OcrRecord, OutboundOrder, OutboundOrderItem, Product, User, Warehouse
from app.schemas import OutboundItemOut, OutboundOrderCreate, OutboundOrderDetailOut, OutboundOrderOut

router = APIRouter()
settings = get_settings()


def _configured() -> bool:
    return get_ai_config().configured


def _generate_order_no(db: Session, tenant_id: int) -> str:
    seq = db.execute(text("SELECT nextval('outbound_order_no_seq')")).scalar()
    return f"CK{tenant_id:04d}{datetime.now().strftime('%Y%m%d')}{seq:06d}"


def _validate_and_enrich(db: Session, tenant_id: int, req: OutboundOrderCreate) -> list[dict]:
    warehouse = db.query(Warehouse).filter(
        Warehouse.id == req.warehouse_id, Warehouse.tenant_id == tenant_id
    ).first()
    if warehouse is None:
        raise HTTPException(status_code=400, detail="出库仓库不属于当前客户")
    if req.destination_warehouse_id is not None:
        destination = db.query(Warehouse).filter(
            Warehouse.id == req.destination_warehouse_id,
            Warehouse.tenant_id == tenant_id,
        ).first()
        if destination is None:
            raise HTTPException(status_code=400, detail="调入仓库不属于当前客户")
    if req.outbound_type == "transfer":
        if req.destination_warehouse_id is None:
            raise HTTPException(status_code=400, detail="调拨出库必须选择调入仓库")
        if req.destination_warehouse_id == req.warehouse_id:
            raise HTTPException(status_code=400, detail="调出仓库与调入仓库不能相同")

    result: list[dict] = []
    for index, item in enumerate(req.items, start=1):
        product = db.query(Product).filter(
            Product.id == item.product_id, Product.tenant_id == tenant_id
        ).first()
        if product is None:
            raise HTTPException(status_code=400, detail=f"第 {index} 行商品不属于当前客户")
        result.append({
            "product_id": product.id,
            "product_name": product.name,
            "sku": product.sku,
            "qty": item.qty,
            "unit": product.unit,
            "remark": item.remark,
        })
    return result


def _create_order(
    db: Session,
    user: User,
    req: OutboundOrderCreate,
    image_path: str | None = None,
) -> OutboundOrder:
    items = _validate_and_enrich(db, user.tenant_id, req)
    order = OutboundOrder(
        tenant_id=user.tenant_id,
        order_no=_generate_order_no(db, user.tenant_id),
        outbound_type=req.outbound_type,
        warehouse_id=req.warehouse_id,
        destination_warehouse_id=req.destination_warehouse_id,
        department=(req.department or "").strip() or None,
        recipient=(req.recipient or "").strip() or None,
        remark=(req.remark or "").strip() or None,
        status="pending_audit",
        source=req.source,
        image_path=image_path,
        created_by=user.id,
    )
    db.add(order)
    db.flush()
    for item in items:
        db.add(OutboundOrderItem(tenant_id=user.tenant_id, order_id=order.id, **item))
    return order


@router.post("/orders", response_model=OutboundOrderOut, status_code=201)
def create_outbound_order(
    req: OutboundOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    order = _create_order(db, user, req)
    db.commit()
    db.refresh(order)
    return order


@router.get("/orders", response_model=list[OutboundOrderOut])
def list_outbound_orders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return db.query(OutboundOrder).filter(
        OutboundOrder.tenant_id == user.tenant_id
    ).order_by(OutboundOrder.id.desc()).limit(100).all()


@router.get("/orders/{order_id}", response_model=OutboundOrderDetailOut)
def get_outbound_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.query(OutboundOrder).filter(
        OutboundOrder.id == order_id, OutboundOrder.tenant_id == user.tenant_id
    ).first()
    if order is None:
        raise HTTPException(status_code=404, detail="出库单不存在")
    result = OutboundOrderOut.model_validate(order).model_dump()
    result["items"] = [
        OutboundItemOut.model_validate(item).model_dump()
        for item in db.query(OutboundOrderItem).filter(
            OutboundOrderItem.order_id == order.id,
            OutboundOrderItem.tenant_id == user.tenant_id,
        ).order_by(OutboundOrderItem.id).all()
    ]
    return result


def _recognize_record(record_id: int, tenant_id: int, image_path: str) -> None:
    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        record = db.query(OcrRecord).filter(
            OcrRecord.id == record_id,
            OcrRecord.tenant_id == tenant_id,
            OcrRecord.document_type == "outbound",
        ).first()
        if record is None:
            return
        try:
            recognized, raw_text = recognize_outbound_image(db, tenant_id, image_path)
            record.raw_text = raw_text
            record.confidence = recognized["overall_confidence"]
            record.fields_json = {"status": "recognized", "recognized": recognized}
        except Exception as exc:
            db.rollback()
            record = db.query(OcrRecord).filter(
                OcrRecord.id == record_id,
                OcrRecord.tenant_id == tenant_id,
                OcrRecord.document_type == "outbound",
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


@router.post("/ocr/upload")
def upload_outbound_image(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    content = file.file.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过大小限制")
    tenant_dir = Path(settings.upload_dir) / str(user.tenant_id) / "outbound"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    file_path = tenant_dir / f"{uuid.uuid4().hex}{ext}"
    file_path.write_bytes(content)
    record = OcrRecord(
        tenant_id=user.tenant_id,
        document_type="outbound",
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
        result["message"] = "图片已保存；请先在设置中配置多模态 API"
        return result
    background_tasks.add_task(_recognize_record, record.id, user.tenant_id, str(file_path))
    result.update(action="processing", message="图片已保存，正在后台识别出库单")
    return result


@router.get("/ocr/{record_id}")
def get_outbound_recognition(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    record = db.query(OcrRecord).filter(
        OcrRecord.id == record_id,
        OcrRecord.tenant_id == user.tenant_id,
        OcrRecord.document_type == "outbound",
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="出库识别记录不存在")
    fields = record.fields_json or {}
    action = fields.get("status") or ("recognized" if fields.get("recognized") else "processing")
    return {
        "ocr_record_id": record.id,
        "image_path": record.image_path,
        "recognized": fields.get("recognized"),
        "action": action,
        "message": fields.get("message") or (
            "识别完成，请核对后生成出库单" if action == "recognized" else "正在后台识别"
        ),
    }


@router.post("/ocr/{record_id}/commit")
def commit_outbound_draft(
    record_id: int,
    req: OutboundOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    record = db.query(OcrRecord).filter(
        OcrRecord.id == record_id,
        OcrRecord.tenant_id == user.tenant_id,
        OcrRecord.document_type == "outbound",
    ).with_for_update().first()
    if record is None:
        raise HTTPException(status_code=404, detail="出库识别记录不存在")
    if record.outbound_order_id:
        order = db.query(OutboundOrder).filter(
            OutboundOrder.id == record.outbound_order_id,
            OutboundOrder.tenant_id == user.tenant_id,
        ).first()
        return {"order": OutboundOrderOut.model_validate(order).model_dump(), "reused": True}
    req.source = "ocr"
    order = _create_order(db, user, req, record.image_path)
    db.flush()
    record.outbound_order_id = order.id
    record.fields_json = {
        **(record.fields_json or {}),
        "submitted": req.model_dump(),
    }
    db.commit()
    db.refresh(order)
    return {"order": OutboundOrderOut.model_validate(order).model_dump(), "reused": False}

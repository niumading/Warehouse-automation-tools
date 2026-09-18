"""委外入库/出库：拍照识别商品数量，人工选择加工商和仓库。"""
from datetime import datetime, timezone
from pathlib import Path
import uuid
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.workflow import recognize_outbound_image
from app.api.ocr import _recognition_error_message
from app.core.ai_config import get_ai_config
from app.core.config import get_settings
from app.core.subcontract_documents import document_time, subcontract_print_html, subcontract_xlsx
from app.core.database import SessionLocal, get_db, set_tenant_context
from app.core.deps import get_current_user, require_role, ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR
from app.models import (
    OcrRecord,
    Product,
    SubcontractOrder,
    SubcontractOrderItem,
    Supplier,
    User,
    Warehouse,
)
from app.schemas import (
    SubcontractItemOut,
    SubcontractOrderCreate,
    SubcontractOrderDetailOut,
    SubcontractOrderOut,
    SubcontractReject,
    SubcontractResubmit,
    SubcontractReviewVersion,
)


router = APIRouter()
settings = get_settings()
VALID_DIRECTIONS = {"inbound", "outbound"}


def _document_type(direction: str) -> str:
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail="委外方向必须是入库或出库")
    return f"subcontract_{direction}"


def _generate_order_no(db: Session, tenant_id: int, direction: str) -> str:
    seq = db.execute(text("SELECT nextval('subcontract_order_no_seq')")).scalar()
    prefix = "WWRK" if direction == "inbound" else "WWCK"
    return f"{prefix}{tenant_id:04d}{datetime.now().strftime('%Y%m%d')}{seq:06d}"


def _validate_and_enrich(db: Session, tenant_id: int, req: SubcontractOrderCreate | SubcontractResubmit) -> list[dict]:
    supplier = db.query(Supplier).filter(
        Supplier.id == req.supplier_id, Supplier.tenant_id == tenant_id
    ).first()
    if supplier is None:
        raise HTTPException(status_code=400, detail="加工商不属于当前客户")
    warehouse = db.query(Warehouse).filter(
        Warehouse.id == req.warehouse_id, Warehouse.tenant_id == tenant_id
    ).first()
    if warehouse is None:
        raise HTTPException(status_code=400, detail="仓库不属于当前客户")

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


def _review_event(db: Session, order: SubcontractOrder, user: User, action: str, reason: str | None = None) -> None:
    """Append immutable snapshots; never rely on client-provided names or history."""
    items = db.query(SubcontractOrderItem).filter(
        SubcontractOrderItem.order_id == order.id,
        SubcontractOrderItem.tenant_id == user.tenant_id,
    ).order_by(SubcontractOrderItem.id).all()
    order.review_history = [*(order.review_history or []), {
        "action": action, "user_id": user.id, "username": user.username,
        "at": datetime.now(timezone.utc).isoformat(), "reason": reason,
        "revision": order.revision, "supplier_id": order.supplier_id,
        "warehouse_id": order.warehouse_id, "remark": order.remark,
        "items": [{"product_id": i.product_id, "product_name": i.product_name,
                   "sku": i.sku, "qty": float(i.qty), "unit": i.unit, "remark": i.remark} for i in items],
    }]


def _locked_order(db: Session, user: User, order_id: int) -> SubcontractOrder:
    order = db.query(SubcontractOrder).filter(
        SubcontractOrder.id == order_id, SubcontractOrder.tenant_id == user.tenant_id
    ).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="委外单不存在")
    return order


def _create_order(
    db: Session,
    user: User,
    req: SubcontractOrderCreate,
    image_path: str | None = None,
) -> SubcontractOrder:
    items = _validate_and_enrich(db, user.tenant_id, req)
    order = SubcontractOrder(
        tenant_id=user.tenant_id,
        order_no=_generate_order_no(db, user.tenant_id, req.direction),
        direction=req.direction,
        supplier_id=req.supplier_id,
        warehouse_id=req.warehouse_id,
        remark=(req.remark or "").strip() or None,
        status="pending_audit",
        source=req.source,
        image_path=image_path,
        created_by=user.id,
    )
    db.add(order)
    db.flush()
    for item in items:
        db.add(SubcontractOrderItem(tenant_id=user.tenant_id, order_id=order.id, **item))
    return order


@router.post("/orders", response_model=SubcontractOrderOut, status_code=201)
def create_subcontract_order(
    req: SubcontractOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    order = _create_order(db, user, req)
    db.commit()
    db.refresh(order)
    return order


@router.get("/orders", response_model=list[SubcontractOrderOut])
def list_subcontract_orders(
    direction: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(SubcontractOrder).filter(SubcontractOrder.tenant_id == user.tenant_id)
    if direction is not None:
        if direction not in VALID_DIRECTIONS:
            raise HTTPException(status_code=400, detail="委外方向必须是入库或出库")
        query = query.filter(SubcontractOrder.direction == direction)
    return query.order_by(SubcontractOrder.id.desc()).limit(100).all()


@router.get("/orders/{order_id}", response_model=SubcontractOrderDetailOut)
def get_subcontract_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = db.query(SubcontractOrder).filter(
        SubcontractOrder.id == order_id, SubcontractOrder.tenant_id == user.tenant_id
    ).first()
    if order is None:
        raise HTTPException(status_code=404, detail="委外单不存在")
    result = SubcontractOrderOut.model_validate(order).model_dump()
    reviewer = db.query(User).filter(
        User.id == order.audited_by, User.tenant_id == user.tenant_id
    ).first() if order.audited_by else None
    result["audited_by_name"] = reviewer.username if reviewer else None
    result["items"] = [
        SubcontractItemOut.model_validate(item).model_dump()
        for item in db.query(SubcontractOrderItem).filter(
            SubcontractOrderItem.order_id == order.id,
            SubcontractOrderItem.tenant_id == user.tenant_id,
        ).order_by(SubcontractOrderItem.id).all()
    ]
    return result


def _subcontract_document(db: Session, user: User, order_id: int) -> dict:
    order = db.query(SubcontractOrder).filter(
        SubcontractOrder.id == order_id, SubcontractOrder.tenant_id == user.tenant_id
    ).first()
    if order is None:
        raise HTTPException(status_code=404, detail="委外单不存在")
    if order.status != "audited":
        raise HTTPException(status_code=409, detail="仅已审核委外单可导出或打印")
    supplier = db.query(Supplier).filter(Supplier.id == order.supplier_id, Supplier.tenant_id == user.tenant_id).first()
    warehouse = db.query(Warehouse).filter(Warehouse.id == order.warehouse_id, Warehouse.tenant_id == user.tenant_id).first()
    def username(user_id):
        actor = db.query(User).filter(User.id == user_id, User.tenant_id == user.tenant_id).first() if user_id else None
        return actor.username if actor else "—"
    items = db.query(SubcontractOrderItem).filter(
        SubcontractOrderItem.order_id == order.id, SubcontractOrderItem.tenant_id == user.tenant_id
    ).order_by(SubcontractOrderItem.id).all()
    if not items:
        raise HTTPException(status_code=409, detail="委外单没有商品明细，不能导出或打印")
    inbound = order.direction == "inbound"
    return {
        "title": "委外入库单" if inbound else "委外出库单", "order_no": order.order_no,
        "warehouse_label": "入库仓库" if inbound else "出库仓库",
        "qty_label": "入库数量" if inbound else "出库数量",
        "supplier_name": supplier.name if supplier else "未指定",
        "warehouse_name": warehouse.name if warehouse else "未指定",
        "created_by_name": username(order.created_by), "created_at": document_time(order.created_at),
        "audited_by_name": username(order.audited_by), "audited_at": document_time(order.audited_at),
        "remark": order.remark,
        "items": [{"sku": i.sku, "product_name": i.product_name, "qty": i.qty,
                   "unit": i.unit, "remark": i.remark} for i in items],
    }


@router.get("/orders/{order_id}/export")
def export_subcontract_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doc = _subcontract_document(db, user, order_id)
    filename = quote(f"{doc['title']}_{doc['order_no']}.xlsx", safe="")
    return Response(subcontract_xlsx(doc), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}", "Cache-Control": "no-store"})


@router.get("/orders/{order_id}/print", response_class=HTMLResponse)
def print_subcontract_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return HTMLResponse(subcontract_print_html(_subcontract_document(db, user, order_id)), headers={"Cache-Control": "no-store"})


@router.post("/orders/{order_id}/audit", response_model=SubcontractOrderOut)
def audit_subcontract_order(
    order_id: int,
    req: SubcontractReviewVersion,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR)),
):
    """只审核本地委外单，不推送 ERP 或变更库存。行锁防止重复审核。"""
    order = db.query(SubcontractOrder).filter(
        SubcontractOrder.id == order_id, SubcontractOrder.tenant_id == user.tenant_id
    ).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="委外单不存在")
    if order.status != "pending_audit":
        raise HTTPException(status_code=400, detail="仅待审核委外单可审核，请刷新状态")
    if order.revision != req.revision:
        raise HTTPException(status_code=409, detail="单据版本已变化，请刷新明细后再审核")
    items = db.query(SubcontractOrderItem).filter(
        SubcontractOrderItem.order_id == order.id,
        SubcontractOrderItem.tenant_id == user.tenant_id,
    ).all()
    if not items or any(item.qty <= 0 for item in items):
        raise HTTPException(status_code=400, detail="委外单商品明细或数量无效，不能审核")
    order.status = "audited"
    order.audited_at = datetime.now(timezone.utc)
    order.audited_by = user.id
    _review_event(db, order, user, "audit")
    order.revision += 1
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/{order_id}/reject", response_model=SubcontractOrderOut)
def reject_subcontract_order(
    order_id: int, req: SubcontractReject,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR)),
):
    order = _locked_order(db, user, order_id)
    if order.status != "pending_audit" or order.revision != req.revision:
        raise HTTPException(status_code=409, detail="单据状态已变化，请刷新后再驳回")
    order.rejection_reason = req.reason.strip()
    order.rejected_by = user.id
    order.rejected_at = datetime.now(timezone.utc)
    _review_event(db, order, user, "reject", order.rejection_reason)
    order.status = "rejected"
    order.revision += 1
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/{order_id}/resubmit", response_model=SubcontractOrderOut)
def resubmit_subcontract_order(
    order_id: int, req: SubcontractResubmit,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    order = _locked_order(db, user, order_id)
    if user.role != ROLE_ADMIN and order.created_by != user.id:
        raise HTTPException(status_code=403, detail="仅原制单人或管理员可修改重新提交")
    if order.status != "rejected" or order.revision != req.revision:
        raise HTTPException(status_code=409, detail="仅被驳回且版本未变化的单据可重新提交，请刷新")
    items = _validate_and_enrich(db, user.tenant_id, req)
    order.supplier_id, order.warehouse_id = req.supplier_id, req.warehouse_id
    order.remark = (req.remark or "").strip() or None
    # ORM replacement preserves historical snapshots and removes only this order's old lines.
    order.items = [SubcontractOrderItem(tenant_id=user.tenant_id, **item) for item in items]
    db.flush()
    _review_event(db, order, user, "resubmit")
    order.status = "pending_audit"
    order.audited_by = None
    order.audited_at = None
    order.revision += 1
    db.commit()
    db.refresh(order)
    return order


def _recognize_record(record_id: int, tenant_id: int, direction: str, image_path: str) -> None:
    db = SessionLocal()
    document_type = f"subcontract_{direction}"
    try:
        set_tenant_context(db, tenant_id)
        record = db.query(OcrRecord).filter(
            OcrRecord.id == record_id,
            OcrRecord.tenant_id == tenant_id,
            OcrRecord.document_type == document_type,
        ).first()
        if record is None:
            return
        try:
            recognized, raw_text = recognize_outbound_image(db, tenant_id, image_path)
            record.raw_text = raw_text
            record.confidence = recognized["overall_confidence"]
            record.fields_json = {
                "status": "recognized",
                "direction": direction,
                "recognized": recognized,
            }
        except Exception as exc:
            db.rollback()
            record = db.query(OcrRecord).filter(
                OcrRecord.id == record_id,
                OcrRecord.tenant_id == tenant_id,
                OcrRecord.document_type == document_type,
            ).first()
            if record is None:
                return
            record.fields_json = {
                "status": "recognition_failed",
                "direction": direction,
                "error": str(exc)[:500],
                "message": _recognition_error_message(exc),
            }
        db.commit()
    finally:
        db.close()


@router.post("/ocr/upload")
def upload_subcontract_image(
    background_tasks: BackgroundTasks,
    direction: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    document_type = _document_type(direction)
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    content = file.file.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过大小限制")
    tenant_dir = Path(settings.upload_dir) / str(user.tenant_id) / "subcontract" / direction
    tenant_dir.mkdir(parents=True, exist_ok=True)
    file_path = tenant_dir / f"{uuid.uuid4().hex}{ext}"
    file_path.write_bytes(content)
    record = OcrRecord(
        tenant_id=user.tenant_id,
        document_type=document_type,
        image_path=str(file_path),
        fields_json=(
            {"status": "processing", "direction": direction}
            if get_ai_config().configured
            else {"status": "needs_configuration", "direction": direction}
        ),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    result = {
        "ocr_record_id": record.id,
        "direction": direction,
        "image_path": str(file_path),
        "recognized": None,
        "action": "needs_configuration",
    }
    if not get_ai_config().configured:
        result["message"] = "图片已保存；请先在设置中配置多模态 API"
        return result
    background_tasks.add_task(
        _recognize_record, record.id, user.tenant_id, direction, str(file_path)
    )
    result.update(action="processing", message="图片已保存，正在后台识别委外单")
    return result


@router.get("/ocr/{record_id}")
def get_subcontract_recognition(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    record = db.query(OcrRecord).filter(
        OcrRecord.id == record_id,
        OcrRecord.tenant_id == user.tenant_id,
        OcrRecord.document_type.in_(("subcontract_inbound", "subcontract_outbound")),
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="委外识别记录不存在")
    fields = record.fields_json or {}
    action = fields.get("status") or ("recognized" if fields.get("recognized") else "processing")
    direction = fields.get("direction") or record.document_type.removeprefix("subcontract_")
    return {
        "ocr_record_id": record.id,
        "direction": direction,
        "image_path": record.image_path,
        "recognized": fields.get("recognized"),
        "action": action,
        "message": fields.get("message") or (
            "识别完成，请选择加工商和仓库后生成委外单"
            if action == "recognized"
            else "正在后台识别"
        ),
    }


@router.post("/ocr/{record_id}/commit")
def commit_subcontract_draft(
    record_id: int,
    req: SubcontractOrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    document_type = _document_type(req.direction)
    record = db.query(OcrRecord).filter(
        OcrRecord.id == record_id,
        OcrRecord.tenant_id == user.tenant_id,
        OcrRecord.document_type == document_type,
    ).with_for_update().first()
    if record is None:
        raise HTTPException(status_code=404, detail="委外识别记录不存在或方向不匹配")
    if record.subcontract_order_id:
        order = db.query(SubcontractOrder).filter(
            SubcontractOrder.id == record.subcontract_order_id,
            SubcontractOrder.tenant_id == user.tenant_id,
        ).first()
        return {"order": SubcontractOrderOut.model_validate(order).model_dump(), "reused": True}
    req.source = "ocr"
    order = _create_order(db, user, req, record.image_path)
    db.flush()
    record.subcontract_order_id = order.id
    record.fields_json = {**(record.fields_json or {}), "submitted": req.model_dump()}
    db.commit()
    db.refresh(order)
    return {"order": SubcontractOrderOut.model_validate(order).model_dump(), "reused": False}

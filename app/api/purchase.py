"""采购入库单路由：创建、审核、反审核与幂等推送 ERP。"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_role, ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR
from app.models import User, PurchaseOrder, PurchaseOrderItem, Supplier, TenantSetting, Warehouse, Product
from app.schemas import ErpPushOut, OrderCreate, OrderOut, OrderDetailOut
from app.services.erp import (
    ErpGatewayError,
    ErpNotConfigured,
    build_purchase_payload,
    get_erp_gateway,
)

router = APIRouter()

# 状态常量
ST_PENDING = "pending_audit"
ST_AUDITED = "audited"
ST_RETURNED = "returned"


def _validate_master(db: Session, tenant_id: int, req: OrderCreate):
    """所有入口在服务端验证主数据归属，不能仅依赖页面下拉选项。"""
    for model, value, label in ((Supplier, req.supplier_id, "供应商"), (Warehouse, req.warehouse_id, "仓库")):
        if value is not None and db.query(model).filter(model.id == value, model.tenant_id == tenant_id).first() is None:
            raise HTTPException(status_code=400, detail=f"{label}不属于当前客户")
    for item in req.items:
        if item.product_id is not None:
            product = db.query(Product).filter(Product.id == item.product_id, Product.tenant_id == tenant_id).first()
            if product is None:
                raise HTTPException(status_code=400, detail="商品不属于当前客户")
            item.product_name, item.sku, item.unit = product.name, product.sku, product.unit
        elif not item.product_name or not item.product_name.strip():
            raise HTTPException(status_code=400, detail="请选择商品或填写商品名称")


def _generate_order_no(db: Session, tenant_id: int) -> str:
    """用数据库序列生成跨租户唯一单据号（并发安全）。"""
    from sqlalchemy import text
    seq = db.execute(text("SELECT nextval('purchase_order_no_seq')")).scalar()
    from datetime import datetime
    date = datetime.now().strftime("%Y%m%d")
    return f"{tenant_id:04d}{date}{seq:06d}"


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(
    req: OrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    _validate_master(db, user.tenant_id, req)
    order = PurchaseOrder(
        tenant_id=user.tenant_id,
        order_no=_generate_order_no(db, user.tenant_id),
        status=ST_PENDING,
        source=req.source,
        supplier_id=req.supplier_id,
        warehouse_id=req.warehouse_id,
        image_path=req.image_path,
        created_by=user.id,
    )
    db.add(order)
    db.flush()  # 拿到 order.id
    for it in req.items:
        db.add(PurchaseOrderItem(
            tenant_id=user.tenant_id, order_id=order.id, **it.model_dump()
        ))
    db.commit()
    db.refresh(order)
    return order


@router.get("/orders", response_model=List[OrderOut])
def list_orders(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id == user.tenant_id)
    if status_filter:
        q = q.filter(PurchaseOrder.status == status_filter)
    return q.order_by(PurchaseOrder.id.desc()).all()


@router.get("/orders/{order_id}", response_model=OrderDetailOut)
def get_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    order = db.query(PurchaseOrder).filter(
        PurchaseOrder.id == order_id, PurchaseOrder.tenant_id == user.tenant_id
    ).first()
    if order is None:
        raise HTTPException(status_code=404, detail="单据不存在")
    from app.schemas import OrderItemOut
    result = OrderOut.model_validate(order).model_dump()
    result["items"] = [OrderItemOut.model_validate(item).model_dump() for item in
                       db.query(PurchaseOrderItem).filter(
                           PurchaseOrderItem.order_id == order.id,
                           PurchaseOrderItem.tenant_id == user.tenant_id,
                       ).order_by(PurchaseOrderItem.id).all()]
    return result


@router.put("/orders/{order_id}", response_model=OrderOut)
def update_order(
    order_id: int,
    req: OrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    order = db.query(PurchaseOrder).filter(
        PurchaseOrder.id == order_id, PurchaseOrder.tenant_id == user.tenant_id
    ).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="单据不存在")
    if order.status != ST_PENDING:
        raise HTTPException(status_code=400, detail="仅待审核单据可修改")
    _validate_master(db, user.tenant_id, req)
    # 仅更新 supplier / warehouse / items（保留 order_no / status）
    order.supplier_id = req.supplier_id
    order.warehouse_id = req.warehouse_id
    # 重建明细
    db.query(PurchaseOrderItem).filter(
        PurchaseOrderItem.order_id == order.id,
        PurchaseOrderItem.tenant_id == user.tenant_id,
    ).delete()
    for it in req.items:
        db.add(PurchaseOrderItem(
            tenant_id=user.tenant_id, order_id=order.id, **it.model_dump()
        ))
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/{order_id}/audit", response_model=OrderOut)
def audit_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR)),
):
    """审核：操作员自审。已审核后不可重复审核。"""
    order = db.query(PurchaseOrder).filter(
        PurchaseOrder.id == order_id, PurchaseOrder.tenant_id == user.tenant_id
    ).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="单据不存在")
    if order.status != ST_PENDING:
        raise HTTPException(status_code=400, detail="仅待审核单据可审核")
    order.status = ST_AUDITED
    order.audited_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/{order_id}/push-erp", response_model=ErpPushOut)
def push_order_to_erp(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    """把已审核采购单幂等推送 ERP；重复请求不会重复建单。"""
    order = db.query(PurchaseOrder).filter(
        PurchaseOrder.id == order_id,
        PurchaseOrder.tenant_id == user.tenant_id,
    ).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="单据不存在")
    if order.status != ST_AUDITED:
        raise HTTPException(status_code=400, detail="仅已审核单据可推送 ERP")
    if order.erp_push_status == "succeeded" and order.erp_order_id:
        return _erp_push_result(order, reused=True)

    tenant_setting = db.get(TenantSetting, user.tenant_id)
    try:
        gateway = get_erp_gateway(tenant_setting)
    except ErpNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    items = db.query(PurchaseOrderItem).filter(
        PurchaseOrderItem.order_id == order.id,
        PurchaseOrderItem.tenant_id == user.tenant_id,
    ).order_by(PurchaseOrderItem.id).all()
    supplier = db.get(Supplier, order.supplier_id) if order.supplier_id else None
    warehouse = db.get(Warehouse, order.warehouse_id) if order.warehouse_id else None
    payload = build_purchase_payload(order, items, supplier, warehouse)

    order.erp_push_status = "pushing"
    order.erp_push_attempts = (order.erp_push_attempts or 0) + 1
    order.erp_last_error = None
    db.flush()
    try:
        result = gateway.push_purchase_order(payload, idempotency_key=order.order_no)
        if not result.external_order_id:
            raise ErpGatewayError("ERP 未返回单据编号")
    except (ErpGatewayError, TimeoutError, ConnectionError) as exc:
        order.erp_push_status = "failed"
        order.erp_last_error = "ERP 推送失败，可使用原幂等键重试"
        db.commit()
        raise HTTPException(status_code=502, detail="ERP 推送失败，可安全重试") from exc

    order.erp_order_id = result.external_order_id
    order.erp_push_status = "succeeded"
    order.erp_last_error = None
    order.erp_pushed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)
    return _erp_push_result(order, reused=False)


def _erp_push_result(order: PurchaseOrder, reused: bool) -> dict:
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "idempotency_key": order.order_no,
        "erp_order_id": order.erp_order_id,
        "erp_push_status": order.erp_push_status,
        "erp_push_attempts": order.erp_push_attempts,
        "reused": reused,
    }


@router.post("/orders/{order_id}/return", response_model=OrderOut)
def return_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR, ROLE_AUDITOR)),
):
    """反审核（退回）：已审核单据退回为待审核，可再修改。"""
    order = db.query(PurchaseOrder).filter(
        PurchaseOrder.id == order_id, PurchaseOrder.tenant_id == user.tenant_id
    ).with_for_update().first()
    if order is None:
        raise HTTPException(status_code=404, detail="单据不存在")
    if order.status != ST_AUDITED:
        raise HTTPException(status_code=400, detail="仅已审核单据可反审核退回")
    if order.erp_push_attempts:
        raise HTTPException(status_code=400, detail="已尝试推送 ERP 的单据不可修改，请直接重试或人工处理")
    order.status = ST_PENDING
    order.audited_at = None
    db.commit()
    db.refresh(order)
    return order

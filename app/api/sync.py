"""ERP 主数据同步路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_role, ROLE_ADMIN
from app.models import Product, Supplier, TenantSetting, User, Warehouse
from app.services.erp import ErpGatewayError, ErpNotConfigured, WdtEnterpriseGateway, get_erp_gateway

router = APIRouter()


@router.post("/erp")
def sync_erp(db: Session = Depends(get_db), user: User = Depends(require_role(ROLE_ADMIN))):
    """从旺店通企业版拉取主数据；仅新增或更新，不自动删除本地资料。"""
    try:
        gateway = get_erp_gateway(db.get(TenantSetting, user.tenant_id))
    except ErpNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not isinstance(gateway, WdtEnterpriseGateway):
        raise HTTPException(status_code=409, detail="当前 ERP 暂不支持主数据同步")
    try:
        master = gateway.fetch_master_data()
    except ErpGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        counts = {
            "suppliers": _sync_suppliers(db, user.tenant_id, master["suppliers"]),
            "warehouses": _sync_warehouses(db, user.tenant_id, master["warehouses"]),
            "products": _sync_products(db, user.tenant_id, master["goods"]),
        }
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="旺店通主数据存在重复编号，本次同步已取消，请重试或检查 ERP 主数据",
        ) from exc
    return {"code": 0, "message": "ok", "data": {"synced": True, **counts}}


def _active(row: dict) -> bool:
    return str(row.get("is_disabled", "0")) != "1" and str(row.get("deleted", "0")) in {"", "0", "None"}


def _sync_suppliers(db: Session, tenant_id: int, rows: list[dict]) -> dict:
    created = updated = skipped = 0
    existing = db.query(Supplier).filter(Supplier.tenant_id == tenant_id).all()
    by_code = {row.code: row for row in existing if row.code}
    by_name = {row.name: row for row in existing if row.name}
    seen_codes: set[str] = set()
    for row in rows:
        code = str(row.get("provider_no") or "").strip()
        name = str(row.get("provider_name") or "").strip()
        if not code or not name or not _active(row):
            skipped += 1
            continue
        if code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)
        record = by_code.get(code) or by_name.get(name)
        if record is None:
            record = Supplier(tenant_id=tenant_id, code=code, name=name)
            db.add(record)
            by_code[code] = record
            by_name[name] = record
            created += 1
        else:
            updated += 1
        record.code = code
        record.name = name
        record.contact = str(row.get("contact") or "").strip() or None
        record.phone = str(row.get("mobile") or row.get("telno") or "").strip() or None
        record.return_addr = str(row.get("address") or "").strip() or None
    return {"created": created, "updated": updated, "skipped": skipped}


def _sync_warehouses(db: Session, tenant_id: int, rows: list[dict]) -> dict:
    created = updated = skipped = 0
    existing = db.query(Warehouse).filter(Warehouse.tenant_id == tenant_id).all()
    by_code = {row.code: row for row in existing if row.code}
    seen_codes: set[str] = set()
    for row in rows:
        code = str(row.get("warehouse_no") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code or not name or not _active(row):
            skipped += 1
            continue
        if code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)
        record = by_code.get(code)
        if record is None:
            record = Warehouse(tenant_id=tenant_id, code=code, name=name)
            db.add(record)
            by_code[code] = record
            created += 1
        else:
            record.name = name
            updated += 1
    return {"created": created, "updated": updated, "skipped": skipped}


def _sync_products(db: Session, tenant_id: int, goods_rows: list[dict]) -> dict:
    created = updated = skipped = 0
    existing = db.query(Product).filter(Product.tenant_id == tenant_id).all()
    by_sku = {row.sku: row for row in existing if row.sku}
    seen_skus: set[str] = set()
    for goods in goods_rows:
        goods_name = str(goods.get("goods_name") or "").strip()
        goods_code = str(goods.get("goods_no") or "").strip() or None
        default_unit = str(goods.get("unit_name") or "").strip() or None
        for spec in goods.get("spec_list") or []:
            sku = str(spec.get("spec_no") or "").strip()
            if not sku or str(spec.get("deleted", "0")) == "1":
                skipped += 1
                continue
            if sku in seen_skus:
                skipped += 1
                continue
            seen_skus.add(sku)
            spec_name = str(spec.get("spec_name") or "").strip()
            name = goods_name or spec_name or sku
            unit = str(spec.get("spec_unit_name") or "").strip() or default_unit
            record = by_sku.get(sku)
            if record is None:
                record = Product(tenant_id=tenant_id, sku=sku, name=name)
                db.add(record)
                by_sku[sku] = record
                created += 1
            else:
                updated += 1
            record.name = name
            record.code = goods_code
            record.spec = spec_name or None
            record.unit = unit
    return {"created": created, "updated": updated, "skipped": skipped}

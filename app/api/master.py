"""主数据路由：供应商 / 商品 / 仓库 / 商品-供应商关联。"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_role
from app.models import User, Supplier, Product, ProductSupplier, Warehouse
from app.schemas import (
    SupplierCreate, SupplierOut, ProductCreate, ProductOut,
    ProductSupplierCreate, WarehouseOut,
)

router = APIRouter()


# ===== 供应商 =====
@router.get("/suppliers", response_model=List[SupplierOut])
def list_suppliers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Supplier).filter(Supplier.tenant_id == user.tenant_id).order_by(Supplier.id).all()


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
def create_supplier(req: SupplierCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    exists = db.query(Supplier).filter(
        Supplier.tenant_id == user.tenant_id, Supplier.name == req.name
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="供应商名已存在")
    obj = Supplier(tenant_id=user.tenant_id, **req.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ===== 商品 =====
@router.get("/products", response_model=List[ProductOut])
def list_products(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Product).filter(Product.tenant_id == user.tenant_id).order_by(Product.id).all()


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(req: ProductCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    exists = db.query(Product).filter(
        Product.tenant_id == user.tenant_id, Product.sku == req.sku
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="SKU 已存在")
    obj = Product(tenant_id=user.tenant_id, **req.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ===== 商品-供应商关联 =====
@router.post("/product-supplier", status_code=201)
def link_product_supplier(
    req: ProductSupplierCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    exists = db.query(ProductSupplier).filter(
        ProductSupplier.tenant_id == user.tenant_id,
        ProductSupplier.product_id == req.product_id,
        ProductSupplier.supplier_id == req.supplier_id,
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="该关联已存在")
    obj = ProductSupplier(tenant_id=user.tenant_id, **req.model_dump())
    db.add(obj)
    db.commit()
    return {"code": 0, "message": "ok", "data": {"linked": True}}


# ===== 仓库 =====
@router.get("/warehouses", response_model=List[WarehouseOut])
def list_warehouses(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Warehouse).filter(Warehouse.tenant_id == user.tenant_id).order_by(Warehouse.id).all()

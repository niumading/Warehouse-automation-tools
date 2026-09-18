"""ORM 模型。对应 TechSpec §2 数据模型。所有业务表带 tenant_id（多租户强制）。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, func, JSON, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tenant(Base):
    """租户 = 客户。"""
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class User(TimestampMixin, Base):
    """用户（主账号 + 部门账号）。"""
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)          # admin | operator | viewer
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)    # 是否主账号
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)     # 停用后保留历史但禁止登录
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    """供应商。"""
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_supplier_tenant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    return_addr: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # 退供应商地址
    contact: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Product(Base):
    """商品快照。"""
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    spec: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductSupplier(Base):
    """商品 ↔ 供应商 关联（核心：识别归属）。"""
    __tablename__ = "product_supplier"
    __table_args__ = (UniqueConstraint("tenant_id", "product_id", "supplier_id", name="uq_ps_tenant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)


class Warehouse(Base):
    """仓库（来自 ERP 自动同步）。"""
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_warehouse_tenant_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PurchaseOrder(Base):
    """采购入库单。"""
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "order_no", name="uq_po_tenant_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    order_no: Mapped[str] = mapped_column(String, nullable=False)
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    warehouse_id: Mapped[Optional[int]] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending_audit")  # pending_audit/audited/returned
    source: Mapped[str] = mapped_column(String, default="manual")          # ocr | manual
    image_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    erp_order_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    erp_push_status: Mapped[str] = mapped_column(String, default="not_pushed")
    erp_push_attempts: Mapped[int] = mapped_column(Integer, default=0)
    erp_last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    erp_pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    audited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["PurchaseOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class PurchaseOrderItem(Base):
    """采购入库单明细。"""
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), nullable=True)
    product_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    qty: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    order: Mapped["PurchaseOrder"] = relationship(back_populates="items")


class OutboundOrder(Base):
    """其他出库单：领用、报废、调拨或其他用途。"""
    __tablename__ = "outbound_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "order_no", name="uq_outbound_tenant_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    order_no: Mapped[str] = mapped_column(String, nullable=False)
    outbound_type: Mapped[str] = mapped_column(String, nullable=False, default="other")
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    destination_warehouse_id: Mapped[Optional[int]] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    recipient: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remark: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending_audit")
    source: Mapped[str] = mapped_column(String, default="ocr")
    image_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    audited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["OutboundOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OutboundOrderItem(Base):
    """其他出库单明细。"""
    __tablename__ = "outbound_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("outbound_orders.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remark: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    order: Mapped["OutboundOrder"] = relationship(back_populates="items")


class SubcontractOrder(Base):
    """委外入库/出库单。加工商复用供应商主数据。"""
    __tablename__ = "subcontract_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "order_no", name="uq_subcontract_tenant_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    order_no: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # inbound | outbound
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    remark: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending_audit")
    source: Mapped[str] = mapped_column(String, default="ocr")
    image_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    audited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["SubcontractOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    audited_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rejected_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    review_history: Mapped[list] = mapped_column(JSON, default=list, nullable=False, server_default="[]")


class SubcontractOrderItem(Base):
    """委外单商品明细。"""
    __tablename__ = "subcontract_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("subcontract_orders.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remark: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    order: Mapped["SubcontractOrder"] = relationship(back_populates="items")


class OcrRecord(Base):
    """识别记录（AI 建议留档 + 纠正 diff 沉淀）。"""
    __tablename__ = "ocr_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    outbound_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("outbound_orders.id"), nullable=True)
    subcontract_order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subcontract_orders.id"), nullable=True)
    document_type: Mapped[str] = mapped_column(String, nullable=False, default="purchase")
    image_path: Mapped[str] = mapped_column(String, nullable=False)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    fields_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 字段级识别 + 纠正 diff
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReturnImportRecord(Base):
    """退货运单导入记录；同一租户内以运单号 + SKU 唯一。"""
    __tablename__ = "return_import_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tracking_no", "sku", name="uq_return_record_tracking_sku"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    tracking_no: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"), nullable=True)
    product_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    supplier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    supplier_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    supplier_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    return_addr: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TenantSetting(Base):
    """系统设置（自动审核开关等）。"""
    __tablename__ = "tenant_settings"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    auto_audit_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    audit_threshold: Mapped[float] = mapped_column(Numeric(5, 4), default=0.95)
    erp_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    erp_base_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    erp_sid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    erp_appkey: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    erp_appsecret: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    erp_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    return_import_mapping: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

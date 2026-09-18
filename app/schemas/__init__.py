"""Pydantic 校验模型。"""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field


# ===== Auth =====
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    is_master: bool


# ===== 用户 =====
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_master: bool
    is_active: bool


class AccountCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)
    role: Literal["operator", "auditor", "viewer"]


class AccountUpdate(BaseModel):
    role: Optional[Literal["operator", "auditor", "viewer"]] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)
    is_active: Optional[bool] = None


class AccountOut(UserOut):
    created_at: datetime


# ===== 主数据 =====
class SupplierCreate(BaseModel):
    name: str
    code: Optional[str] = None
    return_addr: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None


class SupplierOut(SupplierCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ProductCreate(BaseModel):
    sku: str
    name: str
    code: Optional[str] = None
    spec: Optional[str] = None
    unit: Optional[str] = None


class ProductOut(ProductCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ProductSupplierCreate(BaseModel):
    product_id: int
    supplier_id: int


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str


# ===== 采购单 =====
class OrderItemCreate(BaseModel):
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    sku: Optional[str] = None
    qty: float = Field(gt=0, le=9999999999.99, allow_inf_nan=False)
    price: Optional[float] = Field(default=None, ge=0, le=9999999999.99, allow_inf_nan=False)
    unit: Optional[str] = None


class OrderCreate(BaseModel):
    source: Literal["ocr", "manual"] = "manual"
    supplier_id: Optional[int] = None
    warehouse_id: Optional[int] = None
    image_path: Optional[str] = None
    items: list[OrderItemCreate] = Field(min_length=1, max_length=500)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_no: str
    status: str
    source: str
    supplier_id: Optional[int]
    warehouse_id: Optional[int]
    erp_order_id: Optional[str]
    erp_push_status: str
    erp_push_attempts: int
    erp_last_error: Optional[str]
    erp_pushed_at: Optional[datetime]
    created_at: datetime


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: Optional[int]
    product_name: Optional[str]
    sku: Optional[str]
    qty: Optional[float]
    price: Optional[float]
    unit: Optional[str]


class OrderDetailOut(OrderOut):
    items: list[OrderItemOut]


class ErpPushOut(BaseModel):
    order_id: int
    order_no: str
    idempotency_key: str
    erp_order_id: str
    erp_push_status: str
    erp_push_attempts: int
    reused: bool


# ===== 其他出库单 =====
class OutboundItemCreate(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=9999999999.99, allow_inf_nan=False)
    remark: Optional[str] = Field(default=None, max_length=500)


class OutboundOrderCreate(BaseModel):
    source: Literal["ocr", "manual"] = "ocr"
    outbound_type: Literal["requisition", "scrap", "transfer", "other"] = "other"
    warehouse_id: int
    destination_warehouse_id: Optional[int] = None
    department: Optional[str] = Field(default=None, max_length=200)
    recipient: Optional[str] = Field(default=None, max_length=200)
    remark: Optional[str] = Field(default=None, max_length=1000)
    items: list[OutboundItemCreate] = Field(min_length=1, max_length=500)


class OutboundOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_no: str
    outbound_type: str
    warehouse_id: int
    destination_warehouse_id: Optional[int]
    department: Optional[str]
    recipient: Optional[str]
    remark: Optional[str]
    status: str
    source: str
    created_at: datetime


class OutboundItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str
    sku: str
    qty: float
    unit: Optional[str]
    remark: Optional[str]


class OutboundOrderDetailOut(OutboundOrderOut):
    items: list[OutboundItemOut]


# ===== 委外入库/出库单 =====
class SubcontractItemCreate(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=9999999999.99, allow_inf_nan=False)
    remark: Optional[str] = Field(default=None, max_length=500)


class SubcontractOrderCreate(BaseModel):
    source: Literal["ocr", "manual"] = "ocr"
    direction: Literal["inbound", "outbound"]
    supplier_id: int
    warehouse_id: int
    remark: Optional[str] = Field(default=None, max_length=1000)
    items: list[SubcontractItemCreate] = Field(min_length=1, max_length=500)


class SubcontractOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_no: str
    direction: str
    supplier_id: int
    warehouse_id: int
    remark: Optional[str]
    status: str
    source: str
    created_at: datetime
    audited_at: Optional[datetime] = None
    audited_by: Optional[int] = None
    created_by: Optional[int] = None
    rejection_reason: Optional[str] = None
    rejected_by: Optional[int] = None
    rejected_at: Optional[datetime] = None
    revision: int = 1
    review_history: list[dict] = Field(default_factory=list)


class SubcontractReviewVersion(BaseModel):
    revision: int = Field(ge=1)


class SubcontractReject(SubcontractReviewVersion):
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class SubcontractResubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    supplier_id: int
    warehouse_id: int
    remark: Optional[str] = Field(default=None, max_length=1000)
    items: list[SubcontractItemCreate] = Field(min_length=1, max_length=500)


class SubcontractItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str
    sku: str
    qty: float
    unit: Optional[str]
    remark: Optional[str]


class SubcontractOrderDetailOut(SubcontractOrderOut):
    items: list[SubcontractItemOut]
    audited_by_name: Optional[str] = None

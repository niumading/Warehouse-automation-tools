"""ERP 适配器边界：稳定请求结构与远端幂等键。"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.models import PurchaseOrder, PurchaseOrderItem, Supplier, TenantSetting, Warehouse


class ErpNotConfigured(Exception):
    """当前租户尚未配置 ERP。"""


class ErpGatewayError(Exception):
    """ERP 请求已发起但失败，可使用同一幂等键安全重试。"""


@dataclass(frozen=True)
class ErpPushResult:
    external_order_id: str


class ErpGateway(Protocol):
    def push_purchase_order(self, payload: dict, idempotency_key: str) -> ErpPushResult: ...


def _number(value) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)), "f")


def build_purchase_payload(
    order: PurchaseOrder,
    items: list[PurchaseOrderItem],
    supplier: Supplier | None,
    warehouse: Warehouse | None,
) -> dict:
    """仅使用稳定业务字段构造请求；本地 order_no 同时作为远端幂等键。"""
    return {
        "method": "erp.purchasebill.save",
        "business_order_no": order.order_no,
        "supplier": None if supplier is None else {
            "code": supplier.code,
            "name": supplier.name,
        },
        "warehouse": None if warehouse is None else {
            "code": warehouse.code,
            "name": warehouse.name,
        },
        "items": [
            {
                "sku": item.sku,
                "name": item.product_name,
                "qty": _number(item.qty),
                "price": _number(item.price),
                "unit": item.unit,
            }
            for item in items
        ],
    }


class GjpNgpGateway:
    """管家婆 NGP 网关占位；拿到官方签名参数后只需替换此实现。"""

    def __init__(self, base_url: str, appkey: str, appsecret: str, token: str):
        self.base_url = base_url
        self.appkey = appkey
        self.appsecret = appsecret
        self.token = token

    def push_purchase_order(self, payload: dict, idempotency_key: str) -> ErpPushResult:
        raise ErpGatewayError("管家婆 NGP 正式请求尚未启用，请先完成 ISV 授权和接口联调")


def build_wdt_signature(params: dict[str, str], appsecret: str) -> str:
    """按照旺店通企业版标准 API 规则生成 32 位小写 MD5 签名。"""
    parts = []
    for key in sorted(params):
        value = str(params[key])
        key_length = len(key.encode("utf-8"))
        value_length = len(value.encode("utf-8"))
        parts.append(f"{key_length:02d}-{key}:{value_length:04d}-{value}")
    source = ";".join(parts) + appsecret
    return hashlib.md5(source.encode("utf-8")).hexdigest()


def build_wdt_purchase_info(payload: dict, idempotency_key: str) -> dict:
    """把稳定内部单据结构转换为旺店通企业版采购单结构。"""
    supplier = payload.get("supplier") or {}
    warehouse = payload.get("warehouse") or {}
    if not supplier.get("code"):
        raise ErpGatewayError("供应商缺少旺店通供应商编号")
    if not warehouse.get("code"):
        raise ErpGatewayError("仓库缺少旺店通仓库编号")
    details = []
    for index, item in enumerate(payload.get("items") or [], start=1):
        if not item.get("sku"):
            raise ErpGatewayError(f"第 {index} 行商品缺少旺店通商家编码")
        if item.get("qty") in (None, ""):
            raise ErpGatewayError(f"第 {index} 行商品缺少采购数量")
        if item.get("price") in (None, ""):
            raise ErpGatewayError(f"第 {index} 行商品缺少采购单价")
        details.append({"spec_no": item["sku"], "num": item["qty"], "price": item["price"]})
    if not details:
        raise ErpGatewayError("采购单没有商品明细")
    return {
        "provider_no": supplier["code"],
        "warehouse_no": warehouse["code"],
        "outer_no": idempotency_key,
        "is_use_outer_no": "0",
        "is_check": "0",
        "details_list": details,
    }


class WdtEnterpriseGateway:
    """旺店通企业版标准 API 网关。"""

    allowed_hosts = {"sandbox.wangdian.cn", "api.wangdian.cn"}

    def __init__(self, base_url: str, sid: str, appkey: str, appsecret: str, timeout: float = 20.0):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise ErpNotConfigured("旺店通企业版 API 地址必须使用官方 HTTPS 域名")
        self.base_url = base_url.rstrip("/")
        self.sid = sid
        self.appkey = appkey
        self.appsecret = appsecret
        self.timeout = timeout

    @property
    def purchase_url(self) -> str:
        if self.base_url.endswith(".php"):
            return self.base_url
        return f"{self.base_url}/purchase_order_push.php"

    def _request(self, service: str, business_params: dict[str, str] | None = None) -> dict:
        params = {
            "sid": self.sid,
            "appkey": self.appkey,
            "timestamp": str(int(time.time())),
            **(business_params or {}),
        }
        params["sign"] = build_wdt_signature(params, self.appsecret)
        url = self.base_url if self.base_url.endswith(".php") else f"{self.base_url}/{service}"
        try:
            response = httpx.post(url, data=params, timeout=self.timeout)
            response.raise_for_status()
            result = response.json()
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise ErpGatewayError("连接旺店通超时或网络异常") from exc
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise ErpGatewayError("旺店通返回了无效响应") from exc
        if not isinstance(result, dict):
            raise ErpGatewayError("旺店通返回了无效响应")
        if str(result.get("code")) != "0":
            message = str(result.get("message") or "未知业务错误")
            raise ErpGatewayError(f"旺店通接口调用失败：{message}")
        return result

    def _query_all(self, service: str, list_key: str, params: dict[str, str] | None = None) -> list[dict]:
        rows: list[dict] = []
        page_no = 0
        page_size = 100
        while page_no < 1000:
            result = self._request(service, {
                **(params or {}), "page_no": str(page_no), "page_size": str(page_size),
            })
            page = result.get(list_key) or []
            if not isinstance(page, list):
                raise ErpGatewayError(f"旺店通 {service} 返回的数据格式不正确")
            rows.extend(row for row in page if isinstance(row, dict))
            try:
                total = int(result.get("total_count", len(rows)))
            except (TypeError, ValueError):
                total = len(rows)
            if not page or len(rows) >= total or len(page) < page_size:
                break
            page_no += 1
        return rows

    def fetch_master_data(self) -> dict[str, list[dict]]:
        """读取主数据；商品接口按官方限制只拉取最近 30 天变更。"""
        now = datetime.now()
        start = now - timedelta(days=29)
        suppliers = self._query_all(
            "purchase_provider_query.php", "provider_list",
            {"column": "provider_no,provider_name,contact,telno,mobile,address,is_disabled,deleted"},
        )
        warehouses = self._query_all(
            "warehouse_query.php", "warehouses", {"is_disabled": "0"},
        )
        goods = self._query_all(
            "goods_query.php", "goods_list",
            {
                "start_time": start.strftime("%Y-%m-%d 00:00:00"),
                "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        return {"suppliers": suppliers, "warehouses": warehouses, "goods": goods}

    def push_purchase_order(self, payload: dict, idempotency_key: str) -> ErpPushResult:
        purchase_info = build_wdt_purchase_info(payload, idempotency_key)
        result = self._request(
            "purchase_order_push.php",
            {"purchase_info": json.dumps(purchase_info, ensure_ascii=False, separators=(",", ":"))},
        )
        external_order_id = str(result.get("message") or "").strip()
        if not external_order_id:
            raise ErpGatewayError("旺店通未返回采购单号")
        return ErpPushResult(external_order_id)


def get_erp_gateway(tenant_setting: TenantSetting | None) -> ErpGateway:
    settings = get_settings()
    base_url = (tenant_setting.erp_base_url if tenant_setting else None) or settings.erp_base_url
    sid = (tenant_setting.erp_sid if tenant_setting else None) or settings.erp_sid
    appkey = (tenant_setting.erp_appkey if tenant_setting else None) or settings.erp_appkey
    appsecret = (tenant_setting.erp_appsecret if tenant_setting else None) or settings.erp_appsecret
    token = tenant_setting.erp_token if tenant_setting else None
    if tenant_setting is None:
        raise ErpNotConfigured("请先配置 ERP")
    erp_type = tenant_setting.erp_type or settings.erp_type
    if erp_type == "wdt_qyb":
        if not all((base_url, sid, appkey, appsecret)):
            raise ErpNotConfigured("请先配置旺店通 API 地址、SID、AppKey 和 AppSecret")
        return WdtEnterpriseGateway(base_url, sid, appkey, appsecret)
    if erp_type != "gjp":
        raise ErpNotConfigured("当前 ERP 类型暂未支持")
    if not all((base_url, appkey, appsecret, token)):
        raise ErpNotConfigured("请先配置管家婆 API 地址、AppKey、AppSecret 和 TOKEN")
    return GjpNgpGateway(
        base_url,
        appkey,
        appsecret,
        token,
    )

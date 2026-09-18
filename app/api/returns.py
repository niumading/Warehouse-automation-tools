"""退货运单数据导入：智能识别 CSV/XLSX 表头并按运单号 + SKU 更新。"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
import json
import posixpath
import re
from threading import Event, Lock
from typing import Literal
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_role, ROLE_ADMIN, ROLE_OPERATOR
from app.models import Product, ProductSupplier, ReturnImportRecord, Supplier, TenantSetting, User

router = APIRouter()

_IMPORT_EVENTS: dict[tuple[int, str], Event] = {}
_IMPORT_EVENTS_LOCK = Lock()

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_ROWS = 5000
MAX_VOUCHER_TRACKING_NOS = 2000
FIELDS = ("tracking_no", "sku", "qty", "product_name", "supplier_code", "supplier_name", "return_addr")
REQUIRED_FIELDS = ("tracking_no", "sku")
ALIASES = {
    "tracking_no": ("运单号", "物流单号", "快递单号", "快递运单号", "包裹号", "trackingno", "trackingnumber", "waybillno"),
    "sku": ("sku", "商家编码", "商品编码", "商品编号", "货品编码", "货品编号", "规格编码", "商家货号"),
    "qty": ("数量", "商品数量", "货品数量", "退货数量", "件数", "qty", "quantity"),
    "product_name": ("商品名称", "货品名称", "品名", "商品", "货品", "productname"),
    "supplier_code": ("供应商编码", "供应商编号", "供货商编码", "providerno", "suppliercode"),
    "supplier_name": ("供应商名称", "供货商名称", "退货供应商", "供应商", "providername", "suppliername"),
    "return_addr": ("退货地址", "退回地址", "供应商退货地址", "returnaddress", "returnaddr"),
}


def _normalize(value: object) -> str:
    return re.sub(r"[\s_\-—/\\（）()【】\[\]：:]+", "", str(value or "")).lower()


NORMALIZED_ALIASES = {field: {_normalize(alias) for alias in aliases} for field, aliases in ALIASES.items()}


def _tag_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:-2] if re.fullmatch(r"-?\d+\.0", text) else text


def _decode_csv(content: bytes) -> list[list[str]]:
    decoded = None
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("CSV 编码无法识别，请另存为 UTF-8 CSV 或 XLSX")
    sample = decoded[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    return [[_cell_text(cell) for cell in row] for row in csv.reader(StringIO(decoded), dialect)]


def _xlsx_rows(content: bytes) -> list[list[str]]:
    try:
        workbook = ZipFile(BytesIO(content))
    except BadZipFile as exc:
        raise ValueError("XLSX 文件已损坏或格式不正确") from exc
    with workbook:
        total_size = sum(item.file_size for item in workbook.infolist())
        if total_size > MAX_UNCOMPRESSED_SIZE:
            raise ValueError("XLSX 解压后内容过大，请拆分后导入")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter() if _tag_name(node) == "t") for item in root]

        workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
        first_sheet = next((node for node in workbook_root.iter() if _tag_name(node) == "sheet"), None)
        if first_sheet is None:
            raise ValueError("XLSX 中没有工作表")
        relation_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        relations = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        target = next((node.attrib.get("Target") for node in relations if node.attrib.get("Id") == relation_id), None)
        if not target:
            raise ValueError("无法读取 XLSX 的第一个工作表")
        clean_target = target.lstrip("/")
        sheet_path = posixpath.normpath(clean_target if clean_target.startswith("xl/") else posixpath.join("xl", clean_target))
        if not sheet_path.startswith("xl/") or sheet_path not in workbook.namelist():
            raise ValueError("XLSX 工作表路径无效")
        root = ET.fromstring(workbook.read(sheet_path))
        rows: list[list[str]] = []
        for row_node in (node for node in root.iter() if _tag_name(node) == "row"):
            values: dict[int, str] = {}
            for cell in (node for node in row_node if _tag_name(node) == "c"):
                ref = cell.attrib.get("r", "A1")
                letters = re.match(r"[A-Z]+", ref.upper())
                column = 0
                for letter in (letters.group(0) if letters else "A"):
                    column = column * 26 + ord(letter) - 64
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter() if _tag_name(node) == "t")
                else:
                    value_node = next((node for node in cell if _tag_name(node) == "v"), None)
                    value = value_node.text if value_node is not None else ""
                    if cell_type == "s" and value:
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError):
                            value = ""
                values[column - 1] = _cell_text(value)
            width = max(values, default=-1) + 1
            rows.append([values.get(index, "") for index in range(width)])
        return rows


def _read_table(filename: str, content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "csv":
        source_rows = _decode_csv(content)
    elif suffix == "xlsx":
        source_rows = _xlsx_rows(content)
    else:
        raise ValueError("仅支持 .csv 和 .xlsx 文件")
    source_rows = [row for row in source_rows if any(_cell_text(cell) for cell in row)]
    if not source_rows:
        raise ValueError("文件中没有可导入的数据")

    def header_score(row: list[str]) -> int:
        normalized = {_normalize(cell) for cell in row if cell}
        return sum(bool(normalized & aliases) for aliases in NORMALIZED_ALIASES.values())

    header_index = max(range(min(10, len(source_rows))), key=lambda index: header_score(source_rows[index]))
    raw_headers = [_cell_text(cell) or f"未命名列{index + 1}" for index, cell in enumerate(source_rows[header_index])]
    headers: list[str] = []
    seen: dict[str, int] = {}
    for header in raw_headers:
        seen[header] = seen.get(header, 0) + 1
        headers.append(header if seen[header] == 1 else f"{header} ({seen[header]})")
    rows = []
    for source in source_rows[header_index + 1: header_index + 1 + MAX_ROWS]:
        row = {header: _cell_text(source[index]) if index < len(source) else "" for index, header in enumerate(headers)}
        if any(row.values()):
            rows.append(row)
    if not rows:
        raise ValueError("表头下方没有可导入的数据")
    return headers, rows


def _uploaded_table(file: UploadFile) -> tuple[str, list[str], list[dict[str, str]]]:
    filename = (file.filename or "").strip()
    content = file.file.read(MAX_FILE_SIZE + 1)
    if not content:
        raise HTTPException(status_code=400, detail="导入文件不能为空")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="导入文件不能超过 10 MB")
    try:
        headers, rows = _read_table(filename, content)
    except (ValueError, KeyError, ET.ParseError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return filename, headers, rows


def _suggest_mapping(headers: list[str], saved: dict | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    saved = saved or {}
    for field in FIELDS:
        if saved.get(field) in headers:
            mapping[field] = saved[field]
            continue
        mapping[field] = next(
            (header for header in headers if _normalize(header) in NORMALIZED_ALIASES[field]),
            "",
        )
    return mapping


def _mapping_from_json(value: str, headers: list[str]) -> dict[str, str]:
    try:
        mapping = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="字段映射格式错误") from exc
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=422, detail="字段映射格式错误")
    clean = {field: str(mapping.get(field) or "").strip() for field in FIELDS}
    missing = [field for field in REQUIRED_FIELDS if not clean[field]]
    if missing:
        raise HTTPException(status_code=422, detail="必须映射运单号和 SKU")
    if any(header and header not in headers for header in clean.values()):
        raise HTTPException(status_code=422, detail="字段映射与当前文件表头不一致")
    if len([value for value in clean.values() if value]) != len(set(value for value in clean.values() if value)):
        raise HTTPException(status_code=422, detail="同一列不能映射到多个字段")
    return clean


def _number(value: str) -> Decimal:
    try:
        number = Decimal(value.replace(",", "").replace("，", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("数量不是有效数字") from exc
    if not number.is_finite() or number <= 0 or number > Decimal("9999999999.99"):
        raise ValueError("数量必须大于 0 且不能超过 9999999999.99")
    return number.quantize(Decimal("0.01"))


@router.post("/import/preview")
def preview_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    filename, headers, rows = _uploaded_table(file)
    setting = db.get(TenantSetting, user.tenant_id)
    mapping = _suggest_mapping(headers, setting.return_import_mapping if setting else None)
    return {
        "filename": filename,
        "headers": headers,
        "mapping": mapping,
        "required_ready": all(mapping[field] for field in REQUIRED_FIELDS),
        "defaults": {"qty": 1} if not mapping["qty"] else {},
        "total_rows": len(rows),
        "sample": rows[:5],
        "truncated": len(rows) >= MAX_ROWS,
    }


class ReturnImportCancel(BaseModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9-]+$")


class ReturnVoucherRequest(BaseModel):
    """一次扫码批次；顺序用于保持凭证和明细的稳定排列。"""

    tracking_nos: list[str] = Field(min_length=1, max_length=MAX_VOUCHER_TRACKING_NOS)
    layout: Literal["supplier_sheets", "combined_sheet"] = "supplier_sheets"


class _ImportCancelled(Exception):
    pass


@router.post("/import/cancel")
def cancel_import(
    req: ReturnImportCancel,
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    with _IMPORT_EVENTS_LOCK:
        cancel_event = _IMPORT_EVENTS.get((user.tenant_id, req.operation_id))
    if cancel_event:
        cancel_event.set()
    return {"cancelled": bool(cancel_event)}


@router.post("/import/commit")
def commit_import(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    operation_id: str = Form(..., min_length=8, max_length=100, pattern=r"^[A-Za-z0-9-]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR)),
):
    event_key = (user.tenant_id, operation_id)
    cancel_event = Event()
    with _IMPORT_EVENTS_LOCK:
        _IMPORT_EVENTS[event_key] = cancel_event
    try:
        filename, headers, rows = _uploaded_table(file)
        if cancel_event.is_set():
            raise _ImportCancelled
        fields = _mapping_from_json(mapping, headers)
        products = db.query(Product).filter(Product.tenant_id == user.tenant_id).all()
        suppliers = db.query(Supplier).filter(Supplier.tenant_id == user.tenant_id).all()
        links = db.query(ProductSupplier).filter(ProductSupplier.tenant_id == user.tenant_id).all()
        existing = db.query(ReturnImportRecord).filter(ReturnImportRecord.tenant_id == user.tenant_id).all()
        product_by_sku = {_normalize(item.sku): item for item in products}
        supplier_by_code = {_normalize(item.code): item for item in suppliers if item.code}
        supplier_by_name = {_normalize(item.name): item for item in suppliers}
        suppliers_by_product: dict[int, list[Supplier]] = {}
        supplier_ids = {item.id: item for item in suppliers}
        for link in links:
            if link.supplier_id in supplier_ids:
                suppliers_by_product.setdefault(link.product_id, []).append(supplier_ids[link.supplier_id])
        records = {(item.tracking_no, item.sku): item for item in existing}

        created = updated = skipped = matched_product = matched_supplier = 0
        issues: list[dict] = []
        now = datetime.now(timezone.utc)
        for line, row in enumerate(rows, start=2):
            if cancel_event.is_set():
                raise _ImportCancelled
            tracking_no = _cell_text(row.get(fields["tracking_no"], ""))
            sku = _cell_text(row.get(fields["sku"], ""))
            if not tracking_no or not sku:
                skipped += 1
                if len(issues) < 100:
                    issues.append({"line": line, "message": "缺少运单号或 SKU"})
                continue
            try:
                qty = _number(row.get(fields["qty"], "")) if fields["qty"] else Decimal("1.00")
            except ValueError as exc:
                skipped += 1
                if len(issues) < 100:
                    issues.append({"line": line, "message": str(exc)})
                continue
            product = product_by_sku.get(_normalize(sku))
            supplier_code = _cell_text(row.get(fields["supplier_code"], "")) if fields["supplier_code"] else ""
            supplier_name = _cell_text(row.get(fields["supplier_name"], "")) if fields["supplier_name"] else ""
            supplier = supplier_by_code.get(_normalize(supplier_code)) if supplier_code else None
            if supplier is None and supplier_name:
                supplier = supplier_by_name.get(_normalize(supplier_name))
            if supplier is None and product:
                candidates = suppliers_by_product.get(product.id, [])
                supplier = candidates[0] if len(candidates) == 1 else None
            if product:
                matched_product += 1
            if supplier:
                matched_supplier += 1
            product_name = _cell_text(row.get(fields["product_name"], "")) if fields["product_name"] else ""
            imported_return_addr = _cell_text(row.get(fields["return_addr"], "")) if fields["return_addr"] else ""
            key = (tracking_no, sku)
            record = records.get(key)
            if record is None:
                record = ReturnImportRecord(tenant_id=user.tenant_id, tracking_no=tracking_no, sku=sku, qty=qty)
                db.add(record)
                records[key] = record
                created += 1
            else:
                updated += 1
            record.qty = qty
            record.product_id = product.id if product else None
            record.product_name = product_name or (product.name if product else record.product_name)
            record.supplier_id = supplier.id if supplier else None
            record.supplier_code = supplier_code or (supplier.code if supplier else record.supplier_code)
            record.supplier_name = supplier_name or (supplier.name if supplier else record.supplier_name)
            record.return_addr = imported_return_addr or (supplier.return_addr if supplier else record.return_addr)
            record.source_filename = filename[:500]
            record.raw_json = row
            record.updated_at = now

        if cancel_event.is_set():
            raise _ImportCancelled
        setting = db.get(TenantSetting, user.tenant_id)
        if setting is None:
            setting = TenantSetting(tenant_id=user.tenant_id)
            db.add(setting)
        setting.return_import_mapping = fields
        if cancel_event.is_set():
            raise _ImportCancelled
        db.commit()
        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "matched_product": matched_product,
            "matched_supplier": matched_supplier,
            "issues": issues,
            "issue_count": skipped,
        }
    except _ImportCancelled as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="导入已取消，数据库未修改") from exc
    finally:
        with _IMPORT_EVENTS_LOCK:
            if _IMPORT_EVENTS.get(event_key) is cancel_event:
                _IMPORT_EVENTS.pop(event_key, None)


@router.get("/records")
def list_records(
    tracking_no: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(ReturnImportRecord).filter(ReturnImportRecord.tenant_id == user.tenant_id)
    if tracking_no.strip():
        query = query.filter(ReturnImportRecord.tracking_no.contains(tracking_no.strip()))
    records = query.order_by(ReturnImportRecord.updated_at.desc(), ReturnImportRecord.id.desc()).limit(200).all()
    return [{
        "id": item.id,
        "tracking_no": item.tracking_no,
        "sku": item.sku,
        "qty": float(item.qty),
        "product_id": item.product_id,
        "product_name": item.product_name,
        "supplier_id": item.supplier_id,
        "supplier_name": item.supplier_name,
        "return_addr": item.return_addr,
        "source_filename": item.source_filename,
        "updated_at": item.updated_at,
    } for item in records]


def _clean_tracking_nos(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        tracking_no = str(value or "").strip()
        if not tracking_no or len(tracking_no) > 200:
            raise HTTPException(status_code=422, detail="运单号不能为空且不能超过 200 个字符")
        if tracking_no not in seen:
            seen.add(tracking_no)
            result.append(tracking_no)
        if len(result) > MAX_VOUCHER_TRACKING_NOS:
            raise HTTPException(status_code=422, detail=f"一次最多匹配 {MAX_VOUCHER_TRACKING_NOS} 个唯一运单号")
    return result


def _distinct_text(values: list[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _cell_text(value)
        key = _normalize(text)
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _build_return_vouchers(tracking_nos: list[str], db: Session, tenant_id: int) -> dict:
    """按供应商身份归组，保证一个凭证及 Excel Sheet 只包含一家供应商。"""
    tracking_nos = _clean_tracking_nos(tracking_nos)
    position = {value: index for index, value in enumerate(tracking_nos)}
    records = db.query(ReturnImportRecord).filter(
        ReturnImportRecord.tenant_id == tenant_id,
        ReturnImportRecord.tracking_no.in_(tracking_nos),
    ).all()
    records.sort(key=lambda item: (position.get(item.tracking_no, len(position)), item.id))
    found = {item.tracking_no for item in records}
    missing_tracking_nos = [value for value in tracking_nos if value not in found]

    supplier_ids = {item.supplier_id for item in records if item.supplier_id}
    suppliers = db.query(Supplier).filter(
        Supplier.tenant_id == tenant_id,
        Supplier.id.in_(supplier_ids),
    ).all() if supplier_ids else []
    supplier_by_id = {item.id: item for item in suppliers}

    rows: list[dict] = []
    for item in records:
        supplier = supplier_by_id.get(item.supplier_id)
        contact_parts = _distinct_text([
            supplier.contact if supplier else None,
            supplier.phone if supplier else None,
        ])
        rows.append({
            "id": item.id,
            "supplier_id": item.supplier_id,
            "tracking_no": item.tracking_no,
            "sku": item.sku,
            "product_name": item.product_name or "",
            "qty": float(item.qty),
            "supplier_code": item.supplier_code or (supplier.code if supplier else "") or "",
            "supplier_name": item.supplier_name or (supplier.name if supplier else "") or "",
            "return_addr": item.return_addr or (supplier.return_addr if supplier else "") or "",
            "return_contact": " ".join(contact_parts),
        })

    def supplier_key(row: dict) -> tuple[str, str]:
        if row["supplier_id"]:
            return "id", str(row["supplier_id"])
        if _normalize(row["supplier_code"]):
            return "code", _normalize(row["supplier_code"])
        if _normalize(row["supplier_name"]):
            return "name", _normalize(row["supplier_name"])
        if _normalize(row["return_addr"]):
            return "address", _normalize(row["return_addr"])
        return "unknown", str(row["id"])

    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault(supplier_key(row), []).append(row)
    groups = list(grouped.values())

    vouchers = []
    warnings = []
    if missing_tracking_nos:
        warnings.append(f"未找到运单号：{'、'.join(missing_tracking_nos)}")
    for voucher_index, group in enumerate(groups, start=1):
        supplier_names = _distinct_text([item["supplier_name"] for item in group])
        supplier_codes = _distinct_text([item["supplier_code"] for item in group])
        addresses = _distinct_text([item["return_addr"] for item in group])
        contacts = _distinct_text([item["return_contact"] for item in group])
        tracking_values = _distinct_text([item["tracking_no"] for item in group])
        voucher_warnings = []
        if len(supplier_names) > 1:
            voucher_warnings.append("同一供应商编号对应多个名称，请核对数据库")
        if len(addresses) > 1:
            voucher_warnings.append("本组包含多个退货地址，请核对")
        for item_index, item in enumerate(group, start=1):
            missing = []
            if not item["product_name"]:
                missing.append("商品名称")
            if not item["supplier_name"]:
                missing.append("供应商")
            if not item["return_addr"]:
                missing.append("退货地址")
            if missing:
                voucher_warnings.append(f"第 {item_index} 行缺少：{'、'.join(missing)}")
        vouchers.append({
            "index": voucher_index,
            "supplier_name": "、".join(supplier_names),
            "supplier_code": "、".join(supplier_codes),
            "return_addr": "；".join(addresses),
            "return_contact": "；".join(contacts),
            "tracking_nos": tracking_values,
            "warnings": voucher_warnings,
            "items": [{
                "serial": index,
                "tracking_no": item["tracking_no"],
                "sku": item["sku"],
                "product_name": item["product_name"],
                "qty": item["qty"],
                "remark": "" if item["qty"] == 1 else f"数量：{item['qty']:g}",
            } for index, item in enumerate(group, start=1)],
        })
    return {
        "tracking_nos": tracking_nos,
        "missing_tracking_nos": missing_tracking_nos,
        "voucher_count": len(vouchers),
        "sheet_count": len(vouchers),
        "item_count": len(rows),
        "group_rule": "one_supplier_per_sheet",
        "warnings": warnings,
        "vouchers": vouchers,
    }


def _safe_xml_text(value: object) -> str:
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", str(value or ""))
    return xml_escape(text, {'"': "&quot;"})


def _xlsx_text_cell(ref: str, value: object, style: int) -> str:
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_safe_xml_text(value)}</t></is></c>'


def _xlsx_number_cell(ref: str, value: int, style: int) -> str:
    return f'<c r="{ref}" s="{style}" t="n"><v>{value}</v></c>'


def _return_voucher_xlsx(result: dict) -> bytes:
    rows_xml: list[str] = []
    merges: list[str] = []
    row_number = 1
    for voucher in result["vouchers"]:
        title_row = row_number
        rows_xml.append(f'<row r="{title_row}" ht="30" customHeight="1">' + ''.join([
            _xlsx_text_cell(f"A{title_row}", "退供应商邮件凭证", 1),
            *[_xlsx_text_cell(f"{column}{title_row}", "", 1) for column in "BCDE"],
        ]) + '</row>')
        merges.append(f"A{title_row}:E{title_row}")

        meta_row = title_row + 1
        contact_row = title_row + 2
        rows_xml.append(f'<row r="{meta_row}" ht="24" customHeight="1">' + ''.join([
            _xlsx_text_cell(f"A{meta_row}", "供应商名称：", 2),
            _xlsx_text_cell(f"B{meta_row}", voucher["supplier_name"], 3),
            _xlsx_text_cell(f"C{meta_row}", "退货地址：", 2),
            _xlsx_text_cell(f"D{meta_row}", voucher["return_addr"], 3),
            _xlsx_text_cell(f"E{meta_row}", "", 3),
        ]) + '</row>')
        rows_xml.append(f'<row r="{contact_row}" ht="24" customHeight="1">' + ''.join([
            _xlsx_text_cell(f"A{contact_row}", "", 2),
            _xlsx_text_cell(f"B{contact_row}", "", 3),
            _xlsx_text_cell(f"C{contact_row}", "退货联系人：", 2),
            _xlsx_text_cell(f"D{contact_row}", voucher["return_contact"], 3),
            _xlsx_text_cell(f"E{contact_row}", "", 3),
        ]) + '</row>')
        merges.extend([
            f"A{meta_row}:A{contact_row}",
            f"B{meta_row}:B{contact_row}",
            f"D{meta_row}:E{meta_row}",
            f"D{contact_row}:E{contact_row}",
        ])

        header_row = title_row + 3
        headers = ("序号", "单号", "货号", "商品名称", "备注")
        rows_xml.append(f'<row r="{header_row}" ht="23" customHeight="1">' + ''.join(
            _xlsx_text_cell(f"{column}{header_row}", value, 4)
            for column, value in zip("ABCDE", headers)
        ) + '</row>')
        row_number = header_row + 1
        for item in voucher["items"]:
            rows_xml.append(f'<row r="{row_number}" ht="23" customHeight="1">' + ''.join([
                _xlsx_number_cell(f"A{row_number}", item["serial"], 5),
                _xlsx_text_cell(f"B{row_number}", item["tracking_no"], 5),
                _xlsx_text_cell(f"C{row_number}", item["sku"], 5),
                _xlsx_text_cell(f"D{row_number}", item["product_name"], 6),
                _xlsx_text_cell(f"E{row_number}", item["remark"], 6),
            ]) + '</row>')
            row_number += 1
        rows_xml.append(f'<row r="{row_number}" ht="12" customHeight="1"/>')
        row_number += 1

    merge_xml = f'<mergeCells count="{len(merges)}">' + ''.join(f'<mergeCell ref="{value}"/>' for value in merges) + '</mergeCells>'
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols><col min="1" max="1" width="9" customWidth="1"/><col min="2" max="2" width="22" customWidth="1"/><col min="3" max="3" width="19" customWidth="1"/><col min="4" max="4" width="58" customWidth="1"/><col min="5" max="5" width="22" customWidth="1"/></cols>
  <sheetData>{''.join(rows_xml)}</sheetData>{merge_xml}
  <pageMargins left="0.35" right="0.35" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
  <pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>
</worksheet>'''
    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="4"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="20"/><name val="SimSun"/></font><font><sz val="12"/><name val="SimSun"/></font><font><sz val="11"/><name val="SimSun"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FF555555"/></left><right style="thin"><color rgb="FF555555"/></right><top style="thin"><color rgb="FF555555"/></top><bottom style="thin"><color rgb="FF555555"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="7">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="退供应商邮件凭证" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="0"/></workbook>'''
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''')
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        archive.writestr("xl/styles.xml", styles_xml)
    return output.getvalue()


def _return_sheet_names(vouchers: list[dict]) -> list[str]:
    """生成 Excel 可接受、大小写不重复且能识别供应商的 Sheet 名。"""
    names: list[str] = []
    used: set[str] = set()
    for index, voucher in enumerate(vouchers, start=1):
        base = voucher.get("supplier_name") or voucher.get("supplier_code") or f"未匹配供应商{index}"
        base = re.sub(r"[\\/*?:\[\]]", "-", str(base)).strip(" '") or f"供应商{index}"
        base = base[:31]
        name = base
        suffix = 2
        while name.lower() in used:
            tail = f"({suffix})"
            name = f"{base[:31-len(tail)]}{tail}"
            suffix += 1
        used.add(name.lower())
        names.append(name)
    return names


def _return_supplier_sheets_xlsx(result: dict) -> bytes:
    """将每家供应商输出到独立 Sheet；单 Sheet 内沿用凭证版式。"""
    vouchers = result["vouchers"]
    sheet_names = _return_sheet_names(vouchers)
    sheet_xmls: list[bytes] = []
    styles_xml = b""
    for voucher in vouchers:
        single_workbook = _return_voucher_xlsx({"vouchers": [voucher]})
        with ZipFile(BytesIO(single_workbook)) as archive:
            sheet_xmls.append(archive.read("xl/worksheets/sheet1.xml"))
            if not styles_xml:
                styles_xml = archive.read("xl/styles.xml")

    sheets = "".join(
        f'<sheet name="{_safe_xml_text(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    workbook_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView activeTab="0"/></bookViews><sheets>{sheets}</sheets><calcPr calcId="0"/></workbook>'''
    sheet_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheet_xmls) + 1)
    )
    relationships_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{sheet_relationships}<Relationship Id="rId{len(sheet_xmls)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    sheet_types = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheet_xmls) + 1)
    )
    content_types_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>{sheet_types}<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships_xml)
        archive.writestr("xl/styles.xml", styles_xml)
        for index, sheet_xml in enumerate(sheet_xmls, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml)
    return output.getvalue()


@router.post("/vouchers/preview")
def preview_return_vouchers(
    req: ReturnVoucherRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _build_return_vouchers(req.tracking_nos, db, user.tenant_id)


@router.post("/vouchers/import")
def import_return_voucher_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从 CSV/XLSX 的运单号列批量提取、匹配并生成凭证预览。"""
    filename, headers, rows = _uploaded_table(file)
    tracking_column = _suggest_mapping(headers)["tracking_no"]
    if not tracking_column:
        raise HTTPException(status_code=422, detail="文件中未识别到运单号列，请将表头改为“运单号”后重试")
    raw_tracking_nos = [_cell_text(row.get(tracking_column, "")) for row in rows]
    tracking_nos = _clean_tracking_nos([value for value in raw_tracking_nos if value])
    if not tracking_nos:
        raise HTTPException(status_code=422, detail="运单号列中没有可匹配的数据")
    seen_tracking_nos: set[str] = set()
    duplicate_tracking_nos: list[str] = []
    duplicate_seen: set[str] = set()
    for value in raw_tracking_nos:
        if not value:
            continue
        if value in seen_tracking_nos and value not in duplicate_seen:
            duplicate_seen.add(value)
            duplicate_tracking_nos.append(value)
        seen_tracking_nos.add(value)
    result = _build_return_vouchers(tracking_nos, db, user.tenant_id)
    result.update({
        "filename": filename,
        "tracking_column": tracking_column,
        "file_row_count": len(rows),
        "file_tracking_count": len(tracking_nos),
        "file_duplicate_count": len([value for value in raw_tracking_nos if value]) - len(tracking_nos),
        "file_duplicate_tracking_nos": duplicate_tracking_nos,
        "file_empty_count": len([value for value in raw_tracking_nos if not value]),
        "matched_tracking_count": len(tracking_nos) - len(result["missing_tracking_nos"]),
    })
    return result


@router.post("/vouchers/export")
def export_return_vouchers(
    req: ReturnVoucherRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = _build_return_vouchers(req.tracking_nos, db, user.tenant_id)
    if not result["vouchers"]:
        raise HTTPException(status_code=404, detail="所选运单均未在数据库中找到")
    combined = req.layout == "combined_sheet"
    content = _return_voucher_xlsx(result) if combined else _return_supplier_sheets_xlsx(result)
    layout_name = "全部供应商单Sheet" if combined else "按供应商分Sheet"
    filename = f"退供应商邮件凭证_{layout_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/waybill")
def get_waybill_table(
    tracking_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """扫码后按完整运单号查询，并返回可直接制表/打印的数据。"""
    tracking_no = tracking_no.strip()
    if not tracking_no or len(tracking_no) > 200:
        raise HTTPException(status_code=422, detail="请输入有效运单号")
    records = db.query(ReturnImportRecord).filter(
        ReturnImportRecord.tenant_id == user.tenant_id,
        ReturnImportRecord.tracking_no == tracking_no,
    ).order_by(ReturnImportRecord.id).all()
    if not records:
        raise HTTPException(status_code=404, detail=f"数据库中未找到运单号 {tracking_no}，请先导入最新数据")
    items = []
    warnings = []
    total_qty = Decimal("0")
    for index, item in enumerate(records, start=1):
        total_qty += Decimal(item.qty)
        missing = []
        if not item.product_id and not item.product_name:
            missing.append("商品")
        if not item.supplier_id and not item.supplier_name:
            missing.append("供应商")
        if not item.return_addr:
            missing.append("退货地址")
        if missing:
            warnings.append(f"第 {index} 行未匹配：{'、'.join(missing)}")
        items.append({
            "id": item.id,
            "sku": item.sku,
            "product_name": item.product_name,
            "qty": float(item.qty),
            "supplier_code": item.supplier_code,
            "supplier_name": item.supplier_name,
            "return_addr": item.return_addr,
        })
    return {
        "tracking_no": tracking_no,
        "item_count": len(items),
        "total_qty": float(total_qty),
        "complete": not warnings,
        "warnings": warnings,
        "items": items,
    }

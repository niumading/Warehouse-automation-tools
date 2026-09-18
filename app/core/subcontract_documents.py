"""Read-only subcontract Excel and A4 print templates (standard library only)."""
from datetime import timedelta, timezone
from decimal import Decimal
from html import escape
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZipFile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _text(value):
    return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value if value is not None else "")))


def document_time(value):
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def quantity(value):
    text = format(Decimal(str(value)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def subcontract_xlsx(doc):
    """One order per sheet. SKU is always text; quantities are numeric, never formulas."""
    rows, merges = [], []

    def cell(ref, value, style=2, numeric=False):
        if numeric:
            return f'<c r="{ref}" s="{style}" t="n"><v>{quantity(value)}</v></c>'
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_text(value)}</t></is></c>'

    def row(number, values, height=30, style=2, numbers=()):
        rows.append(f'<row r="{number}" ht="{height}" customHeight="1">'+ ''.join(
            cell(f"{column}{number}", value, style, index in numbers)
            for index, (column, value) in enumerate(zip("ABCDEF", values))) + '</row>')

    row(1, [doc["title"], "", "", "", "", ""], 36, 1); merges.append("A1:F1")
    for number, text in enumerate([
        f'单据号：{doc["order_no"]}    状态：已审核',
        f'加工商：{doc["supplier_name"]}    {doc["warehouse_label"]}：{doc["warehouse_name"]}',
        f'制单人：{doc["created_by_name"]}    制单时间：{doc["created_at"]}',
        f'审核人：{doc["audited_by_name"]}    审核时间：{doc["audited_at"]}',
        f'整单备注：{doc["remark"] or "—"}',
    ], 2):
        row(number, [text, "", "", "", "", ""], max(30, 18 * (len(text) // 85 + 1)))
        merges.append(f"A{number}:F{number}")
    row(7, ["序号", "商品编号 / SKU", "商品名称", doc["qty_label"], "单位", "行备注"], 28, 3)
    for number, item in enumerate(doc["items"], 8):
        height = max(30, 18 * max(len(item["product_name"]) // 24 + 1, len(item["remark"] or "") // 20 + 1))
        row(number, [number-7, item["sku"], item["product_name"], item["qty"], item["unit"] or "—", item["remark"] or ""], height, numbers=(0,3))
    footer = 8 + len(doc["items"])
    # Different units cannot be meaningfully summed; report line count instead.
    row(footer, [f'共 {len(doc["items"])} 行商品；本单未推送 ERP、不变更库存。', "", "", "", "", ""])
    merges.append(f"A{footer}:F{footer}")
    row(footer+1, ["经办人：                 仓库确认：                 日期：", "", "", "", "", ""], 36)
    merges.append(f"A{footer+1}:F{footer+1}")
    sheet = f'''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="{NS}"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>
<dimension ref="A1:F{footer+1}"/><sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="7" topLeftCell="A8" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<sheetFormatPr defaultRowHeight="30"/><cols>{''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i,width in enumerate([8,25,42,15,10,32],1))}</cols>
<sheetData>{''.join(rows)}</sheetData><mergeCells count="{len(merges)}">{''.join(f'<mergeCell ref="{m}"/>' for m in merges)}</mergeCells>
<printOptions horizontalCentered="1"/><pageMargins left="0.3" right="0.3" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>
<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>'''
    styles = f'''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{NS}">
<fonts count="3"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="20"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    doc_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    workbook = f'''<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{NS}" xmlns:r="{doc_rel}"><sheets><sheet name="{_text(doc['title'])}" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">'{_text(doc['title'])}'!$A$1:$F${footer+1}</definedName><definedName name="_xlnm.Print_Titles" localSheetId="0">'{_text(doc['title'])}'!$1:$7</definedName></definedNames></workbook>'''
    content_types = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{rel_ns}"><Relationship Id="rId1" Type="{doc_rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{rel_ns}"><Relationship Id="rId1" Type="{doc_rel}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{doc_rel}/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def subcontract_print_html(doc):
    headings = ["序号", "商品编号 / SKU", "商品名称", doc["qty_label"], "单位", "行备注"]
    rows = ''.join('<tr>'+''.join(f'<td>{_text(v)}</td>' for v in [i,item["sku"],item["product_name"],quantity(item["qty"]),item["unit"] or "—",item["remark"] or ""])+'</tr>' for i,item in enumerate(doc["items"],1))
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{_text(doc['title'])} {_text(doc['order_no'])}</title><style>
@page{{size:A4 landscape;margin:12mm}}*{{box-sizing:border-box}}body{{font:13px 'Microsoft YaHei',sans-serif;color:#111;margin:24px;background:white}}h1{{text-align:center;font-size:26px;margin:0 0 20px}}.meta{{display:grid;grid-template-columns:1fr 1fr;gap:8px 20px;margin-bottom:14px}}p{{margin:6px 0;white-space:pre-wrap;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;table-layout:fixed;margin:16px 0}}th,td{{border:1px solid #666;padding:8px;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}}th{{background:#f4f4f4}}thead{{display:table-header-group}}tr{{break-inside:avoid}}th:nth-child(1){{width:6%}}th:nth-child(2){{width:21%}}th:nth-child(3){{width:29%}}th:nth-child(4){{width:11%}}th:nth-child(5){{width:7%}}.signatures{{display:flex;justify-content:space-between;margin-top:28px;break-inside:avoid}}.note{{font-size:11px;color:#555}}button{{padding:10px 20px;margin-bottom:18px}}@media print{{body{{margin:0}}.no-print{{display:none}}}}
</style></head><body><button id="printDocument" class="no-print" type="button">打印 / 保存 PDF</button><h1>{_text(doc['title'])}</h1>
<div class="meta"><p>单据号：{_text(doc['order_no'])}</p><p>状态：已审核</p><p>加工商：{_text(doc['supplier_name'])}</p><p>{_text(doc['warehouse_label'])}：{_text(doc['warehouse_name'])}</p><p>制单人：{_text(doc['created_by_name'])}</p><p>制单时间：{_text(doc['created_at'])}</p><p>审核人：{_text(doc['audited_by_name'])}</p><p>审核时间：{_text(doc['audited_at'])}</p></div>
<p>整单备注：{_text(doc['remark'] or '—')}</p><table><thead><tr>{''.join(f'<th>{_text(h)}</th>' for h in headings)}</tr></thead><tbody>{rows}</tbody></table>
<p class="note">共 {len(doc['items'])} 行商品。本单仅本地审核，未推送 ERP、不变更库存。</p><div class="signatures"><span>经办人：________________</span><span>仓库确认：________________</span><span>日期：________________</span></div></body></html>'''

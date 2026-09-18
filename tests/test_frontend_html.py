"""单文件前端的基础结构检查。"""
import unittest
from html.parser import HTMLParser
from pathlib import Path


class BalancedHtmlParser(HTMLParser):
    void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.void_tags:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            raise AssertionError(f"HTML 标签未配平：期望 {self.stack[-1] if self.stack else '无'}，实际 {tag}")
        self.stack.pop()


class FrontendHtmlTest(unittest.TestCase):
    def test_v2_cache_busts_frontend_assets_after_deployment(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        self.assertRegex(page, r'href="\./theme\.css\?v=\d+"')
        self.assertRegex(page, r'href="\./workspace\.css\?v=\d+"')
        self.assertRegex(page, r'src="\./app\.js\?v=\d+"')

    def test_tags_are_balanced(self):
        for path in (Path("web/index.html"), Path("web-v2/index.html")):
            with self.subTest(path=path):
                parser = BalancedHtmlParser()
                parser.feed(path.read_text(encoding="utf-8"))
                parser.close()
                self.assertEqual(parser.stack, [])

    def test_v2_recognition_uses_background_polling_and_can_resume(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('id="cancelRecognizeBtn"', page)
        self.assertIn("后台识别中", script)
        self.assertIn("/api/ocr/${recordId}", script)
        self.assertIn("继续查询结果", script)
        self.assertNotIn("timeout: 75000", script)
        self.assertIn("cancel-recognition", script)

    def test_v2_rotates_the_uploaded_pixels_not_only_the_preview(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('data-action="rotate-left"', page)
        self.assertIn('data-action="rotate-right"', page)
        self.assertIn("state.imageRotation", script)
        self.assertIn("async function imageForRecognition", script)
        self.assertIn("canvas.toBlob", script)
        self.assertIn("await imageForRecognition()", script)

    def test_v2_api_key_has_explicit_replace_and_retry_controls(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('id="editApiKeyBtn"', page)
        self.assertIn('id="settingsRetry"', page)
        self.assertIn("edit-api-key", script)
        self.assertIn("retry-settings", script)
        self.assertIn("API Key 已更新", script)
        self.assertIn("取消更换", script)
        self.assertIn("请输入新的 API Key", script)

    def test_v2_has_tenant_account_management_and_split_permissions(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('id="accountSection"', page)
        self.assertIn('value="auditor"', page)
        self.assertIn("/api/accounts", script)
        self.assertIn("const canAudit", script)
        self.assertIn("save-account", script)
        self.assertIn("toggle-account", script)

    def test_v2_shows_and_triggers_erp_push(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn("ERP 状态", page)
        self.assertIn("erp_push_status", script)
        self.assertIn("push-erp", script)
        self.assertIn("pushOrderToErp", script)
        self.assertIn("重试推送 ERP", script)
        for field_id in ("erpBaseUrl", "erpSid", "erpAppKey", "erpAppSecret", "erpToken", "erpStatus"):
            self.assertIn(f'id="{field_id}"', page)
        self.assertIn('value="wdt_qyb"', page)
        self.assertIn('id="erpSyncBtn"', page)
        self.assertIn("/api/sync/erp", script)
        self.assertIn("erp_sid", script)
        self.assertIn("erp_appsecret", script)
        self.assertIn("erp_token", script)

    def test_v2_scans_groups_and_exports_return_vouchers(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('id="waybillScanInput"', page)
        self.assertIn('id="returnSheetTabs"', page)
        self.assertIn('id="returnVoucherSheets"', page)
        self.assertIn('id="scannedWaybillList"', page)
        self.assertIn('id="returnVoucherBatchFile"', page)
        self.assertIn('data-action="print-waybill"', page)
        self.assertIn('data-action="export-return-vouchers"', page)
        self.assertIn('data-layout="combined_sheet"', page)
        self.assertIn('data-layout="supplier_sheets"', page)
        self.assertIn("导出全部单 Sheet", page)
        self.assertIn("导出供应商分 Sheet", page)
        self.assertIn("/api/returns/vouchers/preview", script)
        self.assertIn("/api/returns/vouchers/import", script)
        self.assertIn("/api/returns/vouchers/export", script)
        self.assertIn("function renderVoucherBatch", script)
        self.assertIn("function selectReturnSheet", script)
        self.assertIn("select-return-sheet", script)
        self.assertIn("每个 Sheet 只包含一家供应商", script)
        self.assertIn("function importVoucherBatchFile", script)
        self.assertIn("tracking_nos: state.scannedWaybills, layout", script)
        self.assertIn("发现重复运单，已阻止重复加入", script)
        self.assertIn("file_duplicate_tracking_nos", script)
        self.assertIn("与本次已扫描运单重复", script)
        self.assertIn("scannedWaybills", script)
        self.assertIn("popup.print()", script)

    def test_v2_return_import_can_be_cancelled_and_never_spins_forever(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('id="cancelReturnImportBtn"', page)
        self.assertIn("returnImportController", script)
        self.assertIn("cancel-return-import", script)
        self.assertIn("正在更新数据库… 已等待", script)

    def test_v2_database_tools_are_collapsed_until_requested(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('id="returnDatabaseToggle"', page)
        self.assertIn('data-action="toggle-return-database"', page)
        self.assertIn('id="returnDatabasePanel" class="return-database-panel" hidden', page)
        self.assertIn("async function toggleReturnDatabase", script)
        self.assertIn("$('returnDatabasePanel').hidden = true", script)

    def test_v2_other_outbound_has_photo_ocr_edit_and_commit_flow(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        for field_id in (
            "outboundDialog", "outboundFileInput", "outboundType", "outboundWarehouse",
            "outboundItemRows", "outboundSubmit", "outboundOrderRows",
        ):
            self.assertIn(f'id="{field_id}"', page)
        self.assertIn('data-action="outbound-recognize"', page)
        self.assertIn('data-action="outbound-cancel-recognition"', page)
        self.assertIn('data-action="outbound-rotate-left"', page)
        self.assertIn('data-action="outbound-rotate-right"', page)
        self.assertIn("function openOutbound", script)
        self.assertIn("async function recognizeOutbound", script)
        self.assertIn("/api/outbound/ocr/upload", script)
        self.assertIn("/api/outbound/ocr/${recordId}", script)
        self.assertIn("/api/outbound/orders", script)
        self.assertIn("/commit", script)
        self.assertNotIn("其他出库模块尚未开放", script)

    def test_v2_subcontract_inbound_and_outbound_share_photo_workflow(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        style = Path("web-v2/workspace.css").read_text(encoding="utf-8")
        for action in ("subcontract-inbound", "subcontract-outbound"):
            self.assertIn(f'data-action="{action}"', page)
        for field_id in (
            "subcontractDialog", "subcontractFileInput", "subcontractSupplier",
            "subcontractWarehouse", "subcontractItemRows", "subcontractSubmit",
            "subcontractOrderRows",
        ):
            self.assertIn(f'id="{field_id}"', page)
        self.assertIn("function openSubcontract", script)
        self.assertIn("async function recognizeSubcontract", script)
        self.assertIn("form.append('direction', state.subcontractDirection)", script)
        self.assertIn("/api/subcontract/ocr/upload", script)
        self.assertIn("/api/subcontract/ocr/${recordId}", script)
        self.assertIn("/api/subcontract/orders?direction=", script)
        self.assertIn("请选择加工商", script)
        self.assertIn("请选择${state.subcontractDirection === 'inbound' ? '入库' : '出库'}仓库", script)
        self.assertIn('class="compact-business-actions"', page)
        self.assertIn(".compact-business-actions{display:none}", style)
        self.assertIn(".compact-business-actions{display:grid", style)

    def test_v2_subcontract_supports_manual_mode_without_ocr(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        for field_id in ("subcontractModes", "subcontractImageMode", "subcontractModeHint"):
            self.assertIn(f'id="{field_id}"', page)
        for action in ("subcontract-mode-image", "subcontract-mode-manual"):
            self.assertIn(f'data-action="{action}"', page)
        self.assertIn("subcontractMode: 'manual'", script)
        self.assertIn("function setSubcontractMode", script)
        self.assertIn("function validateSubcontract", script)
        self.assertIn("source: state.subcontractMode === 'image' ? 'ocr' : 'manual'", script)
        self.assertIn("api('/api/subcontract/orders', { method: 'POST', body })", script)

    def test_v2_subcontract_has_separate_detail_and_audit_workflow(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('id="subcontractDetailDialog"', page)
        self.assertIn('data-action="subcontract-detail"', script)
        self.assertIn('data-action="subcontract-audit"', script)
        self.assertIn("async function auditSubcontractOrder", script)
        self.assertIn("/api/subcontract/orders/${order.id}/audit", script)
        self.assertIn("$('subcontractForm').hidden = !canWrite()", script)
        self.assertIn("order.status === 'pending_audit' && canAudit()", script)

    def test_v2_home_cards_reveal_in_sequence_and_logo_collapses_them(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        style = Path("web-v2/workspace.css").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('<div class="stage" id="stage">', page)
        self.assertNotIn('<div class="stage working"', page)
        self.assertNotIn('aria-label="主导航"', page)
        self.assertNotIn('class="nav-link"', page)
        self.assertIn('id="startWorkBtn" data-action="reveal-home"', page)
        self.assertIn('class="hero-bubble"', page)
        self.assertEqual(page.count('class="theme-toggle"'), 2)
        self.assertGreaterEqual(page.count('<svg'), 3)
        self.assertIn("function setHomeExpanded", script)
        self.assertIn("case 'home': setHomeExpanded(false)", script)
        self.assertIn("case 'reveal-home': setHomeExpanded(true)", script)
        self.assertIn("@keyframes homeCardReveal", style)
        for delay in (".02s", ".20s", ".38s", ".56s"):
            self.assertIn(delay, style)
        self.assertGreaterEqual(style.count("homeCardReveal .16s"), 4)

    def test_v2_hero_is_animated_transparent_iridescent_bubble(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        style = Path("web-v2/workspace.css").read_text(encoding="utf-8")
        for layer in ("hero-bubble", "bubble-film", "bubble-rim", "bubble-glint", "bubble-pearl"):
            self.assertIn(f'class="{layer}"', page)
        self.assertNotIn('class="hero-bars"', page)
        for animation in ("bubbleMorph", "bubbleIridescence", "bubbleGlint", "bubblePearl"):
            self.assertIn(f"@keyframes {animation}", style)
        self.assertIn("background:transparent;box-shadow:none", style)
        self.assertIn("prefers-reduced-motion:reduce", style)
        self.assertIn("mask-composite:exclude", style)
        self.assertNotIn('class="logo plain"', page)
        self.assertNotIn('class="header-brand-icon"', page)
        self.assertIn("header{justify-content:flex-end}", style)
        self.assertIn("43% 57% 63% 37% / 37% 42% 58% 63%", style)
        self.assertIn("@keyframes bubbleRimFlow", style)

    def test_v2_bubble_has_pointer_and_click_interaction(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        self.assertIn('class="bubble-interaction"', page)
        self.assertIn("function setupBubbleInteraction", script)
        for event in ("pointermove", "pointerleave", "pointercancel"):
            self.assertIn(f"addEventListener('{event}'", script)
        self.assertIn("bubble.animate([", script)
        self.assertIn("scale(1.58,.48)", script)
        self.assertIn("if (reducedMotion.matches || !bubble.animate) return", script)
        self.assertIn("clickAnimation?.cancel()", script)

    def test_v2_settings_are_grouped_into_collapsible_sections(self):
        page = Path("web-v2/index.html").read_text(encoding="utf-8")
        script = Path("web-v2/app.js").read_text(encoding="utf-8")
        for group_id in ("settingsGeneral", "settingsAi", "settingsErp", "settingsAccounts"):
            self.assertIn(f'<details class="settings-group" id="{group_id}">', page)
        self.assertEqual(page.count('<summary><span><b>'), 4)
        self.assertNotIn('<details class="settings-group" id="settingsGeneral" open', page)
        self.assertIn("#settingsDialog details[open]", script)
        self.assertIn("group.removeAttribute('open')", script)


if __name__ == "__main__":
    unittest.main()

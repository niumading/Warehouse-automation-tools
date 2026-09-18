import { api, hasSession, getToken, setToken, clearToken } from './api.js';

const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
const state = { me: null, suppliers: [], products: [], warehouses: [], orders: [], accounts: [], outboundOrders: [], outboundOcr: null, outboundOcrRecordId: null, outboundImage: null, outboundImageUrl: null, outboundImageRotation: 0, outboundController: null, outboundTimer: null, outboundDirty: false, subcontractDirection: 'inbound', subcontractOrders: [], subcontractOcr: null, subcontractOcrRecordId: null, subcontractImage: null, subcontractImageUrl: null, subcontractImageRotation: 0, subcontractController: null, subcontractTimer: null, subcontractDirty: false, returnFile: null, returnPreview: null, returnRecords: [], returnImportController: null, returnImportOperationId: null, returnImportTimer: null, returnImportCancelRequested: false, scannedWaybills: [], returnVouchers: null, returnSheetIndex: 0, mode: 'manual', edit: null, detail: null, ocr: null, ocrRecordId: null, image: null, imageUrl: null, imageRotation: 0, busy: false, dirty: false, filter: 'all', generation: 0, ocrController: null, ocrTimer: null, apiKeyConfigured: false, apiKeyHint: '••••', apiKeyEditing: false };
Object.assign(state, { subcontractMode: 'manual', subcontractDetail: null, subcontractEdit: null });
let subcontractDetailRequestId = 0;
let toastTimer, afterLogin = null, loadId = 0, detailId = 0;
const canWrite = () => ['admin', 'operator'].includes(state.me?.role);
const canAudit = () => ['admin', 'operator', 'auditor'].includes(state.me?.role);
const roleName = role => ({ admin: '管理员', operator: '制单员', auditor: '审核员', viewer: '只读' }[role] || role);
const nameOf = (list, id) => list.find(x => x.id === id)?.name || '未指定';
const statusName = status => ({ pending_audit: '待审核', audited: '已审核', returned: '已退回', rejected: '已驳回' }[status] || status);
const badge = status => `<span class="badge ${escape(status)}">${escape(statusName(status))}</span>`;
const erpStatusName = status => ({ not_pushed: '未推送', pushing: '推送中', failed: '推送失败', succeeded: '已推送' }[status] || '未推送');
const erpBadge = order => `<span class="badge erp-${escape(order.erp_push_status || 'not_pushed')}">${escape(erpStatusName(order.erp_push_status))}</span>${order.erp_order_id ? `<small class="erp-id" title="${escape(order.erp_order_id)}">${escape(order.erp_order_id)}</small>` : ''}`;
const timeText = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
function setHomeExpanded(expanded) {
  $('stage').classList.toggle('working', expanded);
  const button = $('startWorkBtn');
  button.setAttribute('aria-expanded', String(expanded));
  button.innerHTML = expanded ? '工作区已展开 <span aria-hidden="true">✓</span>' : '开始工作 <span aria-hidden="true">↗</span>';
}
function toast(message) { $('toast').textContent = message; $('toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.remove('show'), 4300); }
function show(id) { if (!$(id).open) $(id).showModal(); }
function sound() {
  try {
    const context = new (window.AudioContext || window.webkitAudioContext)();
    const osc = context.createOscillator(), gain = context.createGain();
    osc.connect(gain); gain.connect(context.destination); osc.frequency.value = 660;
    gain.gain.setValueAtTime(.05, context.currentTime); gain.gain.exponentialRampToValueAtTime(.001, context.currentTime + .25);
    osc.start(); osc.stop(context.currentTime + .3); osc.onended = () => context.close();
  } catch { /* 提示音不可用不影响制单。 */ }
}
function closeDialog(dialog) {
  if (state.busy) { toast('操作正在进行，请稍候。'); return; }
  if (dialog.id === 'purchaseDialog' && state.dirty && !confirm('关闭后，本次未提交的填写内容将丢弃。继续关闭？')) return;
  if (dialog.id === 'outboundDialog' && state.outboundDirty && !confirm('关闭后，本次未提交的出库单内容将丢弃。继续关闭？')) return;
  if (dialog.id === 'subcontractDialog' && state.subcontractDirty && !confirm('关闭后，本次未提交的委外单内容将丢弃。继续关闭？')) return;
  dialog.close();
  if (dialog.id === 'purchaseDialog') state.dirty = false;
  if (dialog.id === 'outboundDialog') state.outboundDirty = false;
  if (dialog.id === 'subcontractDialog') state.subcontractDirty = false;
  if (dialog.id === 'detailDialog') detailId++;
  if (dialog.id === 'subcontractDetailDialog') { subcontractDetailRequestId++; state.subcontractDetail = null; }
}
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(dialog); });
});
function updateAccount() {
  $('accountName').textContent = state.me ? `${state.me.username} · ${roleName(state.me.role)}` : '未登录';
  $('accountAction').textContent = state.me ? '退出' : '登录';
  $('newOrderBtn').hidden = !canWrite();
  $('memoText').contentEditable = state.me ? 'true' : 'false';
  $('memoText').textContent = state.me ? (localStorage.getItem(`zhicang-memo-${state.me.id}`) ?? '核对供应商与商品数量，确认后生成采购单。') : '登录后记录工作备忘。';
}
function clearSession() {
  subcontractDetailRequestId++; state.subcontractDetail = null;
  state.returnImportController?.abort();
  state.outboundController?.abort();
  state.subcontractController?.abort();
  if (state.returnImportTimer) clearInterval(state.returnImportTimer);
  clearToken(); state.generation++; loadId++; detailId++;
  state.me = null; state.suppliers = []; state.products = []; state.warehouses = []; state.orders = []; state.accounts = []; state.outboundOrders = []; state.subcontractOrders = []; state.returnFile = null; state.returnPreview = null; state.returnRecords = []; state.scannedWaybills = []; state.returnVouchers = null; state.returnSheetIndex = 0; state.detail = null; state.edit = null; state.ocr = null; state.dirty = false; state.outboundDirty = false; state.subcontractDirty = false;
  clearImage();
  clearOutboundImage();
  clearSubcontractImage();
  document.querySelectorAll('dialog[open]').forEach(d => d.close());
  $('itemRows').replaceChildren(); $('detailContent').replaceChildren(); $('orderRows').replaceChildren(); $('outboundItemRows').replaceChildren(); $('outboundOrderRows').replaceChildren(); $('subcontractItemRows').replaceChildren(); $('subcontractOrderRows').replaceChildren();
  $('returnRecordRows').replaceChildren(); $('returnMapping').hidden = true; $('returnImportResult').hidden = true; $('waybillSheet').hidden = true; $('scannedWaybillBar').hidden = true; $('scannedWaybillList').replaceChildren(); $('returnSheetTabs').replaceChildren(); $('returnVoucherSheets').replaceChildren(); $('returnFile').value = ''; $('returnVoucherBatchFile').value = ''; $('batchVoucherStatus').textContent = '上传含“运单号”列的 CSV / XLSX，最多 2000 个唯一运单号';
  updateAccount(); renderDashboard();
}
window.addEventListener('session-expired', () => { clearSession(); toast('登录已过期，请重新登录。'); show('loginDialog'); });
function requireLogin(action) {
  if (state.me) return true;
  afterLogin = action; $('loginError').textContent = ''; show('loginDialog'); return false;
}
async function loadMaster() {
  const generation = state.generation;
  const [suppliers, products, warehouses] = await Promise.all([api('/api/master/suppliers'), api('/api/master/products'), api('/api/master/warehouses')]);
  if (generation !== state.generation) throw new Error('会话已变化，请重新打开页面。');
  Object.assign(state, { suppliers, products, warehouses });
}
async function refreshOrders() {
  const current = ++loadId, generation = state.generation;
  try {
    const orders = await api('/api/purchase/orders');
    if (current !== loadId || generation !== state.generation) return;
    state.orders = orders; $('ordersError').textContent = ''; renderDashboard(); renderOrders();
  } catch (e) { if (current === loadId) { $('ordersError').textContent = e.message; $('dashboardStatus').textContent = '单据暂未加载，进入单据中心重试'; } }
}
const returnMappingIds = { tracking_no: 'mapTracking', sku: 'mapSku', qty: 'mapQty', product_name: 'mapProductName', supplier_code: 'mapSupplierCode', supplier_name: 'mapSupplierName', return_addr: 'mapReturnAddr' };
const returnRequired = ['tracking_no', 'sku'];
function returnMapping() {
  return Object.fromEntries(Object.entries(returnMappingIds).map(([field,id]) => [field, $(id).value]));
}
function validateReturnMapping(showError = false) {
  const mapping = returnMapping();
  const requiredReady = returnRequired.every(field => mapping[field]);
  const selected = Object.values(mapping).filter(Boolean);
  const unique = selected.length === new Set(selected).size;
  $('returnCommitBtn').disabled = state.busy || !state.returnFile || !requiredReady || !unique;
  if (showError) $('returnImportError').textContent = !requiredReady ? '请选择运单号和 SKU 对应的列。' : !unique ? '同一列不能对应多个字段。' : '';
  return requiredReady && unique;
}
function fillReturnMapping(preview) {
  const options = '<option value="">不导入此字段</option>' + preview.headers.map(header => `<option value="${escape(header)}">${escape(header)}</option>`).join('');
  Object.entries(returnMappingIds).forEach(([field,id]) => { $(id).innerHTML = options; $(id).value = preview.mapping[field] || ''; });
  const visibleHeaders = preview.headers.slice(0, 8);
  $('returnPreviewHead').innerHTML = `<tr>${visibleHeaders.map(header => `<th>${escape(header)}</th>`).join('')}</tr>`;
  $('returnPreviewRows').innerHTML = preview.sample.map(row => `<tr>${visibleHeaders.map(header => `<td>${escape(row[header] || '—')}</td>`).join('')}</tr>`).join('');
  $('returnPreviewTotal').textContent = `识别到 ${preview.total_rows} 行${preview.truncated ? '（最多导入前 5000 行）' : ''}${preview.defaults?.qty === 1 ? ' · 未发现数量列，将按每行 1 件导入' : ''}`;
  $('returnMapping').hidden = false; validateReturnMapping();
}
async function previewReturnFile(file) {
  if (!file || state.busy) return;
  if (!/\.(csv|xlsx)$/i.test(file.name)) { $('returnImportError').textContent = '请选择 CSV 或 XLSX 文件。'; $('returnFile').value = ''; return; }
  if (!file.size || file.size > 10*1024*1024) { $('returnImportError').textContent = '文件需小于 10 MB，且不能为空。'; $('returnFile').value = ''; return; }
  state.busy = true; state.returnFile = file; state.returnPreview = null;
  $('returnImportError').textContent = ''; $('returnImportResult').hidden = true; $('returnMapping').hidden = true; $('returnFileStatus').textContent = `正在读取 ${file.name}…`;
  try {
    const body = new FormData(); body.append('file', file);
    const preview = await api('/api/returns/import/preview', { method: 'POST', body, timeout: 30000 });
    state.returnPreview = preview; fillReturnMapping(preview);
    $('returnFileStatus').textContent = `已读取 ${preview.filename}，请确认字段后导入。`;
  } catch (error) { state.returnFile = null; $('returnFileStatus').textContent = '文件读取失败。'; $('returnImportError').textContent = error.message; }
  finally { state.busy = false; validateReturnMapping(); }
}
async function commitReturnImport() {
  if (state.busy || !canWrite() || !state.returnFile || !validateReturnMapping(true)) return;
  const controller = new AbortController();
  const operationId = globalThis.crypto?.randomUUID?.() || `import-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  state.busy = true; state.returnImportController = controller; state.returnImportOperationId = operationId; state.returnImportCancelRequested = false;
  validateReturnMapping(); $('returnFile').disabled = true; $('returnImportError').textContent = ''; $('returnCommitBtn').textContent = '正在更新数据库…'; $('cancelReturnImportBtn').hidden = false; $('cancelReturnImportBtn').disabled = false; $('cancelReturnImportBtn').textContent = '取消导入';
  const started = Date.now();
  const showElapsed = () => { $('returnCommitBtn').textContent = `正在更新数据库… 已等待 ${Math.floor((Date.now()-started)/1000)} 秒`; };
  showElapsed(); state.returnImportTimer = setInterval(showElapsed, 1000);
  try {
    const body = new FormData(); body.append('file', state.returnFile); body.append('mapping', JSON.stringify(returnMapping())); body.append('operation_id', operationId);
    const result = await api('/api/returns/import/commit', { method: 'POST', body, timeout: 60000, signal: controller.signal });
    const issueText = result.skipped ? `，跳过 ${result.skipped} 行（请检查错误提示）` : '';
    $('returnImportResult').innerHTML = `<b>数据库更新完成</b><br>新增 ${result.created} 条，更新 ${result.updated} 条${issueText}<br>商品匹配 ${result.matched_product} 条，供应商匹配 ${result.matched_supplier} 条${result.issues?.length ? `<br>${result.issues.slice(0,5).map(item => `第 ${item.line} 行：${escape(item.message)}`).join('<br>')}` : ''}`;
    $('returnImportResult').hidden = false; sound(); toast(`退货数据已更新：新增 ${result.created}，更新 ${result.updated}`); await refreshReturnRecords();
  } catch (error) { $('returnImportError').textContent = state.returnImportCancelRequested ? '导入已取消，数据库未修改。' : error.message; }
  finally {
    if (state.returnImportTimer) clearInterval(state.returnImportTimer);
    state.returnImportTimer = null; state.returnImportController = null; state.returnImportOperationId = null; state.busy = false;
    $('returnFile').disabled = !canWrite(); $('cancelReturnImportBtn').hidden = true; $('returnCommitBtn').textContent = '确认导入并更新'; validateReturnMapping();
  }
}
async function cancelReturnImport(button) {
  const controller = state.returnImportController, operationId = state.returnImportOperationId;
  if (!controller || !operationId || state.returnImportCancelRequested) return;
  state.returnImportCancelRequested = true; button.disabled = true; button.textContent = '正在取消…';
  try {
    await api('/api/returns/import/cancel', { method: 'POST', body: { operation_id: operationId }, timeout: 5000 });
  } catch { /* 服务不可用时仍立即结束前端等待。 */ }
  finally { controller.abort(); }
}
function renderReturnRecords() {
  $('returnRecordRows').innerHTML = state.returnRecords.length ? state.returnRecords.map(item => `<tr><td><b>${escape(item.tracking_no)}</b><small>${escape(item.source_filename || '')}</small></td><td>${escape(item.sku)}<small>${escape(item.product_name || '未匹配商品')}</small></td><td>${escape(item.qty)}</td><td>${escape(item.supplier_name || '未匹配供应商')}</td><td>${escape(item.return_addr || '待补充')}</td><td>${escape(timeText(item.updated_at))}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">暂无导入记录</td></tr>';
  $('returnRecordTotal').textContent = `显示 ${state.returnRecords.length} 条最近记录`;
}
async function refreshReturnRecords() {
  const search = $('returnSearch').value.trim();
  state.returnRecords = await api(`/api/returns/records${search ? `?tracking_no=${encodeURIComponent(search)}` : ''}`);
  renderReturnRecords();
}
async function openReturns() {
  if (!requireLogin('returns')) return;
  $('returnImportError').textContent = ''; $('waybillScanError').textContent = ''; show('returnsDialog');
  $('returnDatabasePanel').hidden = true; $('returnDatabaseToggle').setAttribute('aria-expanded', 'false'); $('returnDatabaseToggle').textContent = '数据库导入与查询';
  $('returnFile').disabled = !canWrite(); $('returnCommitBtn').hidden = !canWrite();
  $('returnFileStatus').textContent = canWrite() ? '选择文件后自动预览，不会立即修改数据库。' : '当前账号只能查看已导入的数据。';
  setTimeout(() => $('waybillScanInput').focus(), 0);
}
async function toggleReturnDatabase(button) {
  const panel = $('returnDatabasePanel');
  if (!panel.hidden) {
    panel.hidden = true; button.setAttribute('aria-expanded', 'false'); button.textContent = '数据库导入与查询';
    $('waybillScanInput').focus(); return;
  }
  panel.hidden = false; button.setAttribute('aria-expanded', 'true'); button.textContent = '收起数据库功能';
  state.busy = true; button.disabled = true; $('returnImportError').textContent = '';
  try { await refreshReturnRecords(); }
  catch (error) { $('returnImportError').textContent = error.message; }
  finally { state.busy = false; button.disabled = false; }
}
function renderVoucherBatch(result) {
  state.returnVouchers = result; state.returnSheetIndex = 0;
  $('scannedWaybillBar').hidden = !state.scannedWaybills.length;
  $('scannedWaybillCount').textContent = `${state.scannedWaybills.length} 个运单号`;
  $('scannedWaybillList').innerHTML = state.scannedWaybills.map(trackingNo => `<button type="button" class="scanned-waybill-chip" data-action="remove-scanned-waybill" data-tracking="${escape(trackingNo)}" title="移除此运单">${escape(trackingNo)} ×</button>`).join('');
  $('waybillGroupCount').textContent = `${result.voucher_count} 张凭证`;
  $('returnSheetTabs').innerHTML = result.vouchers.map((voucher,index) => `<button type="button" role="tab" class="return-sheet-tab${index === 0 ? ' active' : ''}" data-action="select-return-sheet" data-index="${index}" aria-selected="${index === 0}" title="${escape(voucher.supplier_name || `供应商 ${index+1}`)}">Sheet${index+1} · ${escape(voucher.supplier_name || '未匹配供应商')}</button>`).join('');
  $('returnVoucherSheets').innerHTML = result.vouchers.map((voucher,index) => {
    const rows = voucher.items.map(item => `<tr><td>${item.serial}</td><td>${escape(item.tracking_no)}</td><td>${escape(item.sku)}</td><td>${escape(item.product_name || '待补充')}</td><td>${escape(item.remark || '')}</td></tr>`).join('');
    const note = voucher.warnings?.length ? `<div class="voucher-note">${voucher.warnings.map(escape).join('；')}</div>` : '';
    return `<article class="return-voucher" data-sheet-index="${index}"${index === 0 ? '' : ' hidden'}><h4>退供应商邮件凭证</h4><div class="voucher-meta"><div class="label">供应商名称：</div><div>${escape(voucher.supplier_name || '待补充')}</div><div class="label">退货地址：</div><div>${escape(voucher.return_addr || '待补充')}</div><div class="label">供应商编号：</div><div>${escape(voucher.supplier_code || '待补充')}</div><div class="label">退货联系人：</div><div>${escape(voucher.return_contact || '待补充')}</div></div><table class="voucher-table"><thead><tr><th>序号</th><th>单号</th><th>货号</th><th>商品名称</th><th>备注</th></tr></thead><tbody>${rows}</tbody></table>${note}</article>`;
  }).join('');
  $('waybillSummary').textContent = `共 ${result.item_count} 条商品记录，自动生成 ${result.sheet_count || result.voucher_count} 个供应商 Sheet；每个 Sheet 只包含一家供应商`;
  $('waybillWarnings').textContent = (result.warnings || []).join('\n');
  $('waybillSheet').hidden = !result.vouchers.length;
}
function selectReturnSheet(button) {
  const index = Number(button.dataset.index);
  if (!Number.isInteger(index) || index < 0 || index >= (state.returnVouchers?.vouchers?.length || 0)) return;
  state.returnSheetIndex = index;
  document.querySelectorAll('.return-sheet-tab').forEach((tab,tabIndex) => { tab.classList.toggle('active', tabIndex === index); tab.setAttribute('aria-selected', String(tabIndex === index)); });
  document.querySelectorAll('.return-voucher[data-sheet-index]').forEach(sheet => { sheet.hidden = Number(sheet.dataset.sheetIndex) !== index; });
}
function clearScannedWaybills() {
  state.scannedWaybills = []; state.returnVouchers = null; state.returnSheetIndex = 0;
  $('scannedWaybillBar').hidden = true; $('waybillSheet').hidden = true; $('waybillScanError').textContent = '';
  $('scannedWaybillList').replaceChildren(); $('returnSheetTabs').replaceChildren(); $('returnVoucherSheets').replaceChildren();
}
async function loadVoucherBatch(trackingNos) {
  return api('/api/returns/vouchers/preview', { method: 'POST', body: { tracking_nos: trackingNos } });
}
async function importVoucherBatchFile(file) {
  if (!file || state.busy) return;
  const input = $('returnVoucherBatchFile'), panel = input.closest('.batch-voucher-import');
  if (!/\.(csv|xlsx)$/i.test(file.name)) { $('waybillScanError').textContent = '批量匹配仅支持 CSV 或 XLSX 文件。'; input.value = ''; return; }
  if (!file.size || file.size > 10*1024*1024) { $('waybillScanError').textContent = '批量匹配文件需小于 10 MB，且不能为空。'; input.value = ''; return; }
  state.busy = true; input.disabled = true; panel.classList.add('busy'); $('waybillScanError').textContent = ''; $('batchVoucherStatus').textContent = `正在读取并匹配 ${file.name}…`;
  try {
    const body = new FormData(); body.append('file', file);
    const imported = await api('/api/returns/vouchers/import', { method: 'POST', body, timeout: 60000 });
    const missing = new Set(imported.missing_tracking_nos || []);
    const matched = imported.tracking_nos.filter(value => !missing.has(value));
    const current = new Set(state.scannedWaybills);
    const repeatedFromCurrent = matched.filter(value => current.has(value));
    if (!matched.length) throw new Error('文件内的运单号均未在数据库中找到，请先导入最新退货数据。');
    const candidate = [...new Set([...state.scannedWaybills, ...matched])];
    const result = state.scannedWaybills.length ? await loadVoucherBatch(candidate) : imported;
    state.scannedWaybills = candidate; renderVoucherBatch(result); sound();
    const details = [`读取 ${imported.file_tracking_count} 个唯一运单`, `匹配 ${imported.matched_tracking_count} 个`];
    if (imported.file_duplicate_count) details.push(`忽略 ${imported.file_duplicate_count} 个重复项`);
    if (missing.size) details.push(`${missing.size} 个未匹配`);
    $('batchVoucherStatus').textContent = `${imported.filename}：${details.join('，')}`;
    const alerts = [];
    if (imported.file_duplicate_count) {
      const values = imported.file_duplicate_tracking_nos || [];
      alerts.push(`文件内发现 ${imported.file_duplicate_count} 条重复运单，已自动忽略${values.length ? `：${values.slice(0, 6).join('、')}${values.length > 6 ? '…' : ''}` : ''}`);
    }
    if (repeatedFromCurrent.length) alerts.push(`与本次已扫描运单重复 ${repeatedFromCurrent.length} 个，未重复加入：${repeatedFromCurrent.slice(0, 6).join('、')}${repeatedFromCurrent.length > 6 ? '…' : ''}`);
    if (missing.size) {
      const values = [...missing];
      alerts.push(`未匹配运单号：${values.slice(0, 10).join('、')}${values.length > 10 ? ` 等 ${values.length} 个` : ''}`);
    }
    $('waybillScanError').textContent = alerts.join('；');
    toast(`批量匹配完成：${imported.matched_tracking_count} 个运单`);
  } catch (error) { $('waybillScanError').textContent = error.message; $('batchVoucherStatus').textContent = '批量匹配未完成，请检查文件后重试。'; }
  finally { state.busy = false; input.disabled = false; input.value = ''; panel.classList.remove('busy'); $('waybillScanInput').focus(); }
}
async function scanWaybill(button) {
  if (state.busy) return;
  const trackingNo = $('waybillScanInput').value.trim();
  if (!trackingNo) { $('waybillScanError').textContent = '请扫描或输入运单号。'; $('waybillScanInput').focus(); return; }
  if (state.scannedWaybills.includes(trackingNo)) {
    $('waybillScanError').textContent = `重复运单号：${trackingNo} 已在本次凭证中，不会重复加入。`;
    toast('发现重复运单，已阻止重复加入。'); $('waybillScanInput').select(); return;
  }
  state.busy = true; $('waybillScanError').textContent = ''; button.disabled = true; button.textContent = '正在归组…';
  try {
    const candidate = [...state.scannedWaybills, trackingNo];
    const result = await loadVoucherBatch(candidate);
    if (result.missing_tracking_nos?.includes(trackingNo)) throw new Error(`数据库中未找到运单号 ${trackingNo}，请先导入最新数据`);
    state.scannedWaybills = candidate; renderVoucherBatch(result); sound();
  } catch (error) { $('waybillScanError').textContent = error.message; }
  finally { state.busy = false; button.disabled = false; button.textContent = '加入本次凭证'; $('waybillScanInput').select(); }
}
async function removeScannedWaybill(button) {
  const trackingNo = button.dataset.tracking;
  const candidate = state.scannedWaybills.filter(value => value !== trackingNo);
  if (!candidate.length) { clearScannedWaybills(); $('waybillScanInput').focus(); return; }
  state.busy = true; button.disabled = true; $('waybillScanError').textContent = '';
  try { const result = await loadVoucherBatch(candidate); state.scannedWaybills = candidate; renderVoucherBatch(result); }
  catch (error) { $('waybillScanError').textContent = error.message; }
  finally { state.busy = false; $('waybillScanInput').focus(); }
}
async function exportReturnVouchers(button) {
  if (!state.scannedWaybills.length || state.busy) return;
  const layout = button.dataset.layout === 'combined_sheet' ? 'combined_sheet' : 'supplier_sheets';
  const idleText = button.textContent;
  state.busy = true; button.disabled = true; button.textContent = '正在生成 Excel…'; $('waybillScanError').textContent = '';
  try {
    const response = await fetch('/api/returns/vouchers/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', Authorization: `Bearer ${getToken()}` },
      body: JSON.stringify({ tracking_nos: state.scannedWaybills, layout }),
    });
    if (!response.ok) {
      let message = `导出失败（${response.status}）`;
      try { const body = await response.json(); message = body?.data?.detail || body?.detail || body?.message || message; } catch { /* 保留状态码提示。 */ }
      if (response.status === 401) window.dispatchEvent(new Event('session-expired'));
      throw new Error(message);
    }
    const disposition = response.headers.get('Content-Disposition') || '';
    const filenameMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : `退供应商邮件凭证_${new Date().toISOString().slice(0,10)}.xlsx`;
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000); toast(layout === 'combined_sheet' ? '全部供应商单 Sheet 已导出。' : '供应商分 Sheet 已导出。');
  } catch (error) { $('waybillScanError').textContent = error.message; }
  finally { state.busy = false; button.disabled = false; button.textContent = idleText; }
}
function printWaybill() {
  const result = state.returnVouchers; if (!result?.vouchers?.length) return;
  const popup = window.open('', '_blank', 'width=1100,height=760');
  if (!popup) { toast('浏览器阻止了打印窗口，请允许弹出窗口后重试。'); return; }
  const vouchers = result.vouchers.map(voucher => `<section class="voucher"><h1>退供应商邮件凭证</h1><table><tr><th>供应商名称：</th><td>${escape(voucher.supplier_name || '')}</td><th>退货地址：</th><td colspan="2">${escape(voucher.return_addr || '')}</td></tr><tr><th>供应商编号：</th><td>${escape(voucher.supplier_code || '')}</td><th>退货联系人：</th><td colspan="2">${escape(voucher.return_contact || '')}</td></tr><tr><th>序号</th><th>单号</th><th>货号</th><th>商品名称</th><th>备注</th></tr>${voucher.items.map(item => `<tr><td>${item.serial}</td><td>${escape(item.tracking_no)}</td><td>${escape(item.sku)}</td><td>${escape(item.product_name)}</td><td>${escape(item.remark)}</td></tr>`).join('')}</table></section>`).join('');
  popup.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>退供应商邮件凭证</title><style>@page{size:A4 landscape;margin:10mm}body{font-family:SimSun,'宋体',serif;color:#111;margin:0}.voucher{break-inside:avoid;break-after:page;margin:0}.voucher:last-child{break-after:auto}h1{text-align:center;font-size:22px;font-weight:400;margin:0;padding:8px;border:1px solid #333;border-bottom:0}table{width:100%;border-collapse:collapse;font-size:12px}th,td{border:1px solid #333;padding:7px;text-align:left}th{text-align:center;font-weight:400}tr:nth-child(n+3) td:first-child,tr:nth-child(n+3) td:nth-child(2),tr:nth-child(n+3) td:nth-child(3){text-align:center}</style></head><body>${vouchers}</body></html>`);
  popup.document.close(); popup.focus(); setTimeout(() => popup.print(), 150);
}
function renderDashboard() {
  const now = new Date().toDateString();
  $('pendingCount').textContent = state.me ? state.orders.filter(o => o.status === 'pending_audit').length : '—';
  $('auditedCount').textContent = state.me ? state.orders.filter(o => o.status === 'audited').length : '—';
  $('todayCount').textContent = state.me ? state.orders.filter(o => new Date(o.created_at).toDateString() === now).length : '—';
  $('dashboardStatus').textContent = state.me ? `${state.orders.length} 张采购单 · 点击查看详情` : '登录后查看真实单据';
}
$('loginForm').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy) return;
  state.busy = true; $('loginSubmit').disabled = true; $('loginSubmit').textContent = '正在登录…'; $('loginError').textContent = '';
  try {
    const result = await api('/api/auth/login', { method: 'POST', body: { username: $('loginUser').value.trim(), password: $('loginPass').value } });
    setToken(result.access_token);
    state.me = await api('/api/auth/me'); updateAccount();
    $('loginPass').value = ''; $('loginDialog').close();
    await loadMaster(); await refreshOrders();
  } catch (e) {
    if (!state.me) clearToken();
    $('loginError').textContent = e.message;
    if (!$('loginDialog').open) toast(e.message);
  } finally { state.busy = false; $('loginSubmit').disabled = false; $('loginSubmit').textContent = '登录工作台'; }
  if (state.me && afterLogin) { const action = afterLogin; afterLogin = null; await act(action); }
});
function fillSelect(id, list, value, placeholder) {
  $(id).innerHTML = `<option value="">${placeholder}</option>` + list.map(x => `<option value="${x.id}">${escape(x.name)}${x.spec ? ' · ' + escape(x.spec) : ''}</option>`).join('');
  $(id).value = value ?? '';
}
function clearImage() {
  if (state.ocrTimer) clearInterval(state.ocrTimer);
  state.ocrTimer = null; state.ocrController = null;
  if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
  state.imageUrl = null; state.image = null; state.ocr = null; state.ocrRecordId = null; state.imageRotation = 0;
  $('fileInput').value = ''; $('previewImg').removeAttribute('src'); $('previewImg').style.transform = ''; $('imagePreview').hidden = true; $('recognizeBtn').disabled = true; $('recognizeBtn').textContent = '开始识别'; $('cancelRecognizeBtn').hidden = true;
}
function resetPurchase(mode = 'manual') {
  clearImage(); state.edit = null; state.mode = mode; state.dirty = false;
  $('purchaseTitle').textContent = '新建采购入库单'; $('purchaseFeedback').textContent = '';
  $('confidenceNote').hidden = true; $('purchaseModes').hidden = false;
  fillSelect('supplierSelect', state.suppliers, '', '请选择供应商'); fillSelect('warehouseSelect', state.warehouses, '', '请选择仓库');
  $('supplierSelect').classList.remove('needs-review'); $('itemRows').replaceChildren(); addItem(); state.dirty = false; updateMode();
}
function updateMode() {
  const image = state.mode === 'image';
  $('uploadSection').hidden = !image || !!state.edit;
  $('modeImage').classList.toggle('active', image); $('modeManual').classList.toggle('active', !image);
  $('modeImage').setAttribute('aria-pressed', String(image)); $('modeManual').setAttribute('aria-pressed', String(!image));
  $('purchaseSubmit').textContent = state.edit ? '保存修改' : '生成采购单';
  $('purchaseSubmit').disabled = state.busy || (image && !state.ocr && !state.edit);
  $('purchaseFootnote').textContent = image ? '请核对全部字段；修改过的识别结果按后端规则进入人工审核。' : '生成后进入待审核，审核不会执行仓库上架。';
}
async function openPurchase() {
  if (!requireLogin('purchase')) return;
  if (!canWrite()) { toast('当前账号只有查看权限。'); return; }
  if ($('purchaseDialog').open) return;
  await loadMaster(); resetPurchase(); show('purchaseDialog');
  if (!state.products.length || !state.suppliers.length || !state.warehouses.length) $('purchaseFeedback').textContent = '主数据尚不完整，请先由管理员配置商品、供应商与仓库。';
}
function addItem(item = {}) {
  const row = document.createElement('tr');
  row.classList.toggle('low-confidence', !!item.needs_review);
  row.innerHTML = `<td><select class="product" required aria-label="商品"><option value="">请选择商品</option>${state.products.map(p => `<option value="${p.id}">${escape(p.name)}${p.spec ? ' · '+escape(p.spec) : ''} · ${escape(p.sku)}</option>`).join('')}</select><small class="raw-name"></small></td><td><input class="qty" aria-label="数量" type="number" min="0.01" step="0.01" max="9999999999.99" required></td><td class="unit"></td><td><input class="price" aria-label="单价" type="number" min="0" step="0.01" max="9999999999.99" placeholder="—"></td><td><button class="remove-row" type="button" data-action="remove-item" aria-label="移除此商品">×</button></td>`;
  row.querySelector('.product').value = item.product_id ?? '';
  row.querySelector('.qty').value = item.qty ?? '';
  row.querySelector('.price').value = item.price ?? '';
  row.querySelector('.raw-name').textContent = item.raw_name ? `原图：${item.raw_name}${item.needs_review ? ' · 请核对' : ''}` : (item.product_name && !state.products.some(p => p.id === item.product_id) ? `原商品：${item.product_name}，请重新匹配` : '');
  row.querySelector('.unit').textContent = state.products.find(p => p.id === item.product_id)?.unit || item.unit || '—';
  row.querySelector('.product').addEventListener('change', () => { row.querySelector('.unit').textContent = state.products.find(p => p.id === Number(row.querySelector('.product').value))?.unit || '—'; });
  $('itemRows').append(row); state.dirty = true;
}
$('purchaseForm').addEventListener('input', () => { state.dirty = true; });
$('supplierSelect').addEventListener('change', () => $('supplierSelect').classList.remove('needs-review'));
function orderBody() {
  const supplier_id = Number($('supplierSelect').value), warehouse_id = Number($('warehouseSelect').value);
  if (!supplier_id || !warehouse_id) throw new Error('请选择供应商与入库仓库。');
  const items = [...$('itemRows').rows].map((row, index) => {
    const product = state.products.find(p => p.id === Number(row.querySelector('.product').value));
    const qty = Number(row.querySelector('.qty').value), priceText = row.querySelector('.price').value, price = priceText === '' ? null : Number(priceText);
    if (!product || !Number.isFinite(qty) || qty <= 0 || (price !== null && (!Number.isFinite(price) || price < 0))) throw new Error(`第 ${index+1} 行商品或数量、单价无效。`);
    return { product_id: product.id, product_name: product.name, sku: product.sku, unit: product.unit, qty, price };
  });
  if (!items.length) throw new Error('请至少添加一行商品。');
  return { source: state.edit?.source || (state.mode === 'image' ? 'ocr' : 'manual'), supplier_id, warehouse_id, items };
}
$('purchaseForm').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy || !canWrite()) return;
  try {
    if (state.mode === 'image' && !state.ocr && !state.edit) throw new Error('请先完成图片识别，或切换手动制单。');
    const body = orderBody(), edit = state.edit;
    state.busy = true; updateMode(); $('purchaseSubmit').textContent = '正在保存…'; $('purchaseFeedback').textContent = '';
    const path = edit ? `/api/purchase/orders/${edit.id}` : state.mode === 'image' ? `/api/ocr/${state.ocr.ocr_record_id}/commit` : '/api/purchase/orders';
    const result = await api(path, { method: edit ? 'PUT' : 'POST', body });
    const order = result.order || result;
    state.dirty = false; $('purchaseDialog').close(); $('detailDialog').close(); state.detail = null;
    clearImage(); sound(); toast(`${edit ? '已保存' : '已生成'} ${order.order_no} · ${statusName(order.status)}`);
    show('ordersDialog'); await refreshOrders();
  } catch (e) { $('purchaseFeedback').textContent = e.message; }
  finally { state.busy = false; updateMode(); }
});
function chooseImage(file) {
  if (state.busy) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast('请选择 JPG、PNG 或 WebP 图片。'); return; }
  if (!file.size || file.size > 10*1024*1024) { toast('图片需小于 10 MB，且不能为空。'); return; }
  clearImage(); state.image = file; state.imageUrl = URL.createObjectURL(file); state.dirty = true;
  $('previewImg').src = state.imageUrl; $('imageFilename').textContent = file.name;
  $('imageStatus').textContent = '图片已选择，等待开始识别'; $('imagePreview').hidden = false; $('recognizeBtn').disabled = false;
  $('imageRotationStatus').textContent = '方向：原图';
  $('confidenceNote').hidden = true; $('itemRows').replaceChildren(); addItem(); updateMode();
}

function rotateImage(delta) {
  if (!state.image) return;
  state.imageRotation = (state.imageRotation + delta + 360) % 360;
  $('previewImg').style.transform = `rotate(${state.imageRotation}deg)`;
  $('imageRotationStatus').textContent = state.imageRotation ? `方向：已旋转 ${state.imageRotation}°` : '方向：原图';
  if (state.ocr || state.ocrRecordId) {
    state.ocr = null; state.ocrRecordId = null;
    fillSelect('supplierSelect', state.suppliers, '', '请选择供应商');
    fillSelect('warehouseSelect', state.warehouses, '', '请选择仓库');
    $('itemRows').replaceChildren(); addItem(); $('confidenceNote').hidden = true;
  }
  $('imageStatus').textContent = '方向已调整，请开始识别';
  $('recognizeBtn').textContent = '开始识别'; $('recognizeBtn').disabled = false;
  state.dirty = true; updateMode();
}

async function imageForRecognition() {
  if (!state.imageRotation) return state.image;
  const bitmap = await createImageBitmap(state.image);
  const swap = state.imageRotation === 90 || state.imageRotation === 270;
  const canvas = document.createElement('canvas');
  canvas.width = swap ? bitmap.height : bitmap.width;
  canvas.height = swap ? bitmap.width : bitmap.height;
  const context = canvas.getContext('2d');
  context.translate(canvas.width / 2, canvas.height / 2);
  context.rotate(state.imageRotation * Math.PI / 180);
  context.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);
  bitmap.close?.();
  const type = ['image/jpeg', 'image/png', 'image/webp'].includes(state.image.type) ? state.image.type : 'image/jpeg';
  const blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('图片旋转失败，请重新选择图片。')), type, .95));
  return new File([blob], state.image.name, { type, lastModified: Date.now() });
}
$('fileInput').addEventListener('change', e => { if (e.target.files[0]) chooseImage(e.target.files[0]); });
['dragover', 'dragenter'].forEach(type => $('imageDrop').addEventListener(type, e => { e.preventDefault(); $('imageDrop').classList.add('drag'); }));
['drop', 'dragleave'].forEach(type => $('imageDrop').addEventListener(type, e => { e.preventDefault(); $('imageDrop').classList.remove('drag'); if (type === 'drop' && e.dataTransfer.files[0]) chooseImage(e.dataTransfer.files[0]); }));
async function recognize() {
  if (!state.image || state.busy || !canWrite()) return;
  const controller = new AbortController(); state.ocrController = controller;
  state.busy = true; $('fileInput').disabled = true; $('recognizeBtn').disabled = true; updateMode();
  $('cancelRecognizeBtn').hidden = false;
  const started = Date.now();
  const updateWait = () => { const elapsed = Math.floor((Date.now() - started) / 1000); $('imageStatus').textContent = `后台识别中… 已等待 ${elapsed} 秒，通常需要 1–2 分钟`; };
  updateWait(); state.ocrTimer = setInterval(updateWait, 1000);
  try {
    let result;
    if (state.ocrRecordId) {
      result = await api(`/api/ocr/${state.ocrRecordId}`, { signal: controller.signal });
    } else {
      const form = new FormData(); form.append('file', await imageForRecognition());
      result = await api('/api/ocr/upload', { method: 'POST', body: form, timeout: 20000, signal: controller.signal });
      state.ocrRecordId = result.ocr_record_id;
    }
    const recordId = state.ocrRecordId;
    while (result.action === 'processing' && Date.now() - started < 180000) {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 2000);
        controller.signal.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
      });
      result = await api(`/api/ocr/${recordId}`, { timeout: 25000, signal: controller.signal });
    }
    if (result.action === 'processing') {
      const timeout = new Error('后台仍在识别，可点击“继续查询结果”。'); timeout.code = 'TIMEOUT'; throw timeout;
    }
    if (result.action !== 'recognized' || !result.recognized) {
      throw new Error(result.action === 'needs_configuration' ? '图片已保存，但模型尚未配置。请联系管理员，或切换手动制单。' : (result.message || '图片已保存，但识别失败。请检查 API 设置或切换手动制单。'));
    }
    state.ocr = result;
    const draft = result.recognized;
    fillSelect('supplierSelect', state.suppliers, draft.supplier?.supplier_id, '请选择供应商'); fillSelect('warehouseSelect', state.warehouses, draft.warehouse?.warehouse_id, '请选择仓库');
    $('supplierSelect').classList.toggle('needs-review', !!draft.supplier?.needs_review);
    $('itemRows').replaceChildren(); draft.items.forEach(item => addItem(item)); if (!draft.items.length) addItem();
    $('confidenceNote').textContent = `整体置信度 ${Math.round((draft.overall_confidence || 0)*100)}% · 黄色字段请重点核对，其他字段也需确认。`;
    $('confidenceNote').hidden = false; $('imageStatus').textContent = `识别完成 · 记录 #${result.ocr_record_id}`;
  } catch (e) { $('imageStatus').textContent = e.code === 'CANCELLED' ? '已停止等待，识别仍在后台进行。可点击“继续查询结果”。' : e.message; }
  finally {
    if (state.ocrTimer) clearInterval(state.ocrTimer);
    state.ocrTimer = null; state.ocrController = null; state.busy = false;
    $('cancelRecognizeBtn').hidden = true; $('fileInput').disabled = false;
    $('recognizeBtn').disabled = !state.image || !!state.ocr;
    $('recognizeBtn').textContent = state.ocr ? '识别完成' : state.ocrRecordId ? '继续查询结果' : '开始识别';
    updateMode();
  }
}
const outboundTypeName = value => ({ requisition: '领用出库', scrap: '报废出库', transfer: '调拨出库', other: '其他出库' }[value] || '其他出库');
function clearOutboundImage() {
  if (state.outboundTimer) clearInterval(state.outboundTimer);
  state.outboundTimer = null; state.outboundController = null;
  if (state.outboundImageUrl) URL.revokeObjectURL(state.outboundImageUrl);
  state.outboundImageUrl = null; state.outboundImage = null; state.outboundOcr = null; state.outboundOcrRecordId = null; state.outboundImageRotation = 0;
  $('outboundFileInput').value = ''; $('outboundPreviewImg').removeAttribute('src'); $('outboundPreviewImg').style.transform = ''; $('outboundImagePreview').hidden = true;
  $('outboundRecognizeBtn').disabled = true; $('outboundRecognizeBtn').textContent = '开始识别'; $('outboundCancelRecognize').hidden = true;
}
function addOutboundItem(item = {}) {
  const row = document.createElement('tr');
  row.classList.toggle('low-confidence', !!item.needs_review);
  row.innerHTML = `<td><select class="product" required aria-label="商品"><option value="">请选择商品</option>${state.products.map(p => `<option value="${p.id}">${escape(p.name)}${p.spec ? ' · '+escape(p.spec) : ''} · ${escape(p.sku)}</option>`).join('')}</select><small class="raw-name"></small></td><td><input class="qty" aria-label="出库数量" type="number" min="0.01" step="0.01" max="9999999999.99" required></td><td class="unit"></td><td><input class="remark" aria-label="行备注" maxlength="500" placeholder="选填"></td><td><button class="remove-row" type="button" data-action="remove-outbound-item" aria-label="移除此商品">×</button></td>`;
  row.querySelector('.product').value = item.product_id ?? '';
  row.querySelector('.qty').value = item.qty ?? '';
  row.querySelector('.remark').value = item.remark ?? '';
  row.querySelector('.raw-name').textContent = item.raw_name ? `原图：${item.raw_name}${item.needs_review ? ' · 请核对' : ''}` : '';
  row.querySelector('.unit').textContent = state.products.find(p => p.id === item.product_id)?.unit || item.unit || '—';
  row.querySelector('.product').addEventListener('change', () => { row.querySelector('.unit').textContent = state.products.find(p => p.id === Number(row.querySelector('.product').value))?.unit || '—'; row.classList.remove('low-confidence'); });
  $('outboundItemRows').append(row); state.outboundDirty = true;
}
function resetOutbound() {
  clearOutboundImage();
  $('outboundFeedback').textContent = ''; $('outboundConfidence').hidden = true;
  $('outboundType').value = 'requisition'; $('outboundDepartment').value = ''; $('outboundRecipient').value = ''; $('outboundRemark').value = '';
  fillSelect('outboundWarehouse', state.warehouses, '', '请选择出库仓库');
  fillSelect('outboundDestination', state.warehouses, '', '非调拨可不选');
  $('outboundWarehouse').classList.remove('needs-review'); $('outboundDestination').classList.remove('needs-review');
  $('outboundItemRows').replaceChildren(); addOutboundItem(); state.outboundDirty = false; $('outboundSubmit').disabled = true;
}
async function refreshOutboundOrders() {
  state.outboundOrders = await api('/api/outbound/orders'); renderOutboundOrders();
}
function renderOutboundOrders() {
  $('outboundOrderRows').innerHTML = state.outboundOrders.length ? state.outboundOrders.map(order => `<tr><td>${escape(order.order_no)}</td><td>${escape(outboundTypeName(order.outbound_type))}</td><td>${escape(nameOf(state.warehouses, order.warehouse_id))}</td><td>${escape(order.department || '—')}<small>${escape(order.recipient || '—')}</small></td><td>${escape(statusName(order.status))}</td><td>${escape(timeText(order.created_at))}</td></tr>`).join('') : '<tr><td colspan="6" class="empty-cell">暂无其他出库单</td></tr>';
  $('outboundOrderTotal').textContent = `显示 ${state.outboundOrders.length} 张最近出库单`;
}
async function openOutbound() {
  if (!requireLogin('outbound')) return;
  if (!canWrite()) { toast('当前账号只有查看权限。'); return; }
  if ($('outboundDialog').open) return;
  await loadMaster(); resetOutbound(); show('outboundDialog');
  try { await refreshOutboundOrders(); } catch (error) { $('outboundFeedback').textContent = `历史出库单暂未加载：${error.message}`; }
  if (!state.products.length || !state.warehouses.length) $('outboundFeedback').textContent = '商品或仓库主数据尚不完整，请先由管理员同步主数据。';
}
function outboundBody() {
  const outbound_type = $('outboundType').value;
  const warehouse_id = Number($('outboundWarehouse').value);
  const destinationText = $('outboundDestination').value;
  const destination_warehouse_id = destinationText ? Number(destinationText) : null;
  if (!warehouse_id) throw new Error('请选择出库仓库。');
  if (outbound_type === 'transfer' && !destination_warehouse_id) throw new Error('调拨出库必须选择调入仓库。');
  if (outbound_type === 'transfer' && destination_warehouse_id === warehouse_id) throw new Error('调出仓库与调入仓库不能相同。');
  const items = [...$('outboundItemRows').rows].map((row, index) => {
    const product_id = Number(row.querySelector('.product').value), qty = Number(row.querySelector('.qty').value);
    if (!product_id || !Number.isFinite(qty) || qty <= 0) throw new Error(`第 ${index+1} 行商品或数量无效。`);
    return { product_id, qty, remark: row.querySelector('.remark').value.trim() || null };
  });
  if (!items.length) throw new Error('请至少添加一行商品。');
  return { source: 'ocr', outbound_type, warehouse_id, destination_warehouse_id, department: $('outboundDepartment').value.trim() || null, recipient: $('outboundRecipient').value.trim() || null, remark: $('outboundRemark').value.trim() || null, items };
}
$('outboundForm').addEventListener('input', () => { state.outboundDirty = true; });
$('outboundForm').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy || !canWrite()) return;
  try {
    if (!state.outboundOcr) throw new Error('请先完成图片识别。');
    const body = outboundBody(); state.busy = true; $('outboundSubmit').disabled = true; $('outboundSubmit').textContent = '正在生成…'; $('outboundFeedback').textContent = '';
    const result = await api(`/api/outbound/ocr/${state.outboundOcr.ocr_record_id}/commit`, { method: 'POST', body });
    const order = result.order || result; sound(); toast(`已生成出库单 ${order.order_no} · 待审核`);
    state.outboundDirty = false; resetOutbound(); await refreshOutboundOrders();
  } catch (error) { $('outboundFeedback').textContent = error.message; }
  finally { state.busy = false; $('outboundSubmit').textContent = '生成出库单'; $('outboundSubmit').disabled = !state.outboundOcr; }
});
function chooseOutboundImage(file) {
  if (state.busy) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast('请选择 JPG、PNG 或 WebP 图片。'); return; }
  if (!file.size || file.size > 10*1024*1024) { toast('图片需小于 10 MB，且不能为空。'); return; }
  clearOutboundImage(); state.outboundImage = file; state.outboundImageUrl = URL.createObjectURL(file); state.outboundDirty = true;
  $('outboundPreviewImg').src = state.outboundImageUrl; $('outboundImageFilename').textContent = file.name; $('outboundRotationStatus').textContent = '方向：原图';
  $('outboundImageStatus').textContent = '图片已选择，等待开始识别'; $('outboundImagePreview').hidden = false; $('outboundRecognizeBtn').disabled = false; $('outboundSubmit').disabled = true;
}
function rotateOutboundImage(delta) {
  if (!state.outboundImage) return;
  state.outboundImageRotation = (state.outboundImageRotation + delta + 360) % 360;
  $('outboundPreviewImg').style.transform = `rotate(${state.outboundImageRotation}deg)`;
  $('outboundRotationStatus').textContent = state.outboundImageRotation ? `方向：已旋转 ${state.outboundImageRotation}°` : '方向：原图';
  state.outboundOcr = null; state.outboundOcrRecordId = null; $('outboundSubmit').disabled = true; $('outboundConfidence').hidden = true;
  $('outboundImageStatus').textContent = '方向已调整，请重新识别'; $('outboundRecognizeBtn').textContent = '开始识别'; $('outboundRecognizeBtn').disabled = false; state.outboundDirty = true;
}
async function outboundImageForRecognition() {
  if (!state.outboundImageRotation) return state.outboundImage;
  const bitmap = await createImageBitmap(state.outboundImage), swap = state.outboundImageRotation === 90 || state.outboundImageRotation === 270;
  const canvas = document.createElement('canvas'); canvas.width = swap ? bitmap.height : bitmap.width; canvas.height = swap ? bitmap.width : bitmap.height;
  const context = canvas.getContext('2d'); context.translate(canvas.width/2, canvas.height/2); context.rotate(state.outboundImageRotation*Math.PI/180); context.drawImage(bitmap,-bitmap.width/2,-bitmap.height/2); bitmap.close?.();
  const type = ['image/jpeg','image/png','image/webp'].includes(state.outboundImage.type) ? state.outboundImage.type : 'image/jpeg';
  const blob = await new Promise((resolve,reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('图片旋转失败，请重新选择图片。')), type, .95));
  return new File([blob], state.outboundImage.name, { type, lastModified: Date.now() });
}
$('outboundFileInput').addEventListener('change', event => { if (event.target.files[0]) chooseOutboundImage(event.target.files[0]); });
['dragover','dragenter'].forEach(type => $('outboundImageDrop').addEventListener(type, event => { event.preventDefault(); $('outboundImageDrop').classList.add('drag'); }));
['drop','dragleave'].forEach(type => $('outboundImageDrop').addEventListener(type, event => { event.preventDefault(); $('outboundImageDrop').classList.remove('drag'); if (type === 'drop' && event.dataTransfer.files[0]) chooseOutboundImage(event.dataTransfer.files[0]); }));
async function recognizeOutbound() {
  if (!state.outboundImage || state.busy || !canWrite()) return;
  const controller = new AbortController(); state.outboundController = controller; state.busy = true; $('outboundFileInput').disabled = true; $('outboundRecognizeBtn').disabled = true; $('outboundCancelRecognize').hidden = false;
  const started = Date.now(); const updateWait = () => { $('outboundImageStatus').textContent = `后台识别中… 已等待 ${Math.floor((Date.now()-started)/1000)} 秒，通常需要 1–2 分钟`; };
  updateWait(); state.outboundTimer = setInterval(updateWait, 1000);
  try {
    let result;
    if (state.outboundOcrRecordId) result = await api(`/api/outbound/ocr/${state.outboundOcrRecordId}`, { signal: controller.signal });
    else {
      const form = new FormData(); form.append('file', await outboundImageForRecognition());
      result = await api('/api/outbound/ocr/upload', { method: 'POST', body: form, timeout: 20000, signal: controller.signal }); state.outboundOcrRecordId = result.ocr_record_id;
    }
    const recordId = state.outboundOcrRecordId;
    while (result.action === 'processing' && Date.now()-started < 180000) {
      await new Promise((resolve,reject) => { const timer=setTimeout(resolve,2000); controller.signal.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));},{once:true}); });
      result = await api(`/api/outbound/ocr/${recordId}`, { timeout: 25000, signal: controller.signal });
    }
    if (result.action === 'processing') throw new Error('后台仍在识别，可点击“继续查询结果”。');
    if (result.action !== 'recognized' || !result.recognized) throw new Error(result.action === 'needs_configuration' ? '图片已保存，但模型尚未配置。请联系管理员。' : (result.message || '出库单识别失败，请检查 API 设置后重试。'));
    state.outboundOcr = result; const draft = result.recognized;
    $('outboundType').value = draft.outbound_type || 'other'; fillSelect('outboundWarehouse', state.warehouses, draft.warehouse?.warehouse_id, '请选择出库仓库'); fillSelect('outboundDestination', state.warehouses, draft.destination_warehouse?.warehouse_id, '非调拨可不选');
    $('outboundWarehouse').classList.toggle('needs-review', !!draft.warehouse?.needs_review); $('outboundDestination').classList.toggle('needs-review', !!draft.destination_warehouse?.needs_review);
    $('outboundDepartment').value = draft.department || ''; $('outboundRecipient').value = draft.recipient || ''; $('outboundRemark').value = draft.remark || '';
    $('outboundItemRows').replaceChildren(); draft.items.forEach(item => addOutboundItem(item)); if (!draft.items.length) addOutboundItem();
    $('outboundConfidence').textContent = `整体置信度 ${Math.round((draft.overall_confidence || 0)*100)}% · 黄色字段必须核对，确认后才会生成待审核单。`; $('outboundConfidence').hidden = false;
    $('outboundImageStatus').textContent = `识别完成 · 记录 #${result.ocr_record_id}`; $('outboundSubmit').disabled = false;
  } catch (error) { $('outboundImageStatus').textContent = error.name === 'AbortError' ? '已停止等待，识别仍可能在后台进行。可点击“继续查询结果”。' : error.message; }
  finally {
    if (state.outboundTimer) clearInterval(state.outboundTimer); state.outboundTimer = null; state.outboundController = null; state.busy = false; $('outboundCancelRecognize').hidden = true; $('outboundFileInput').disabled = false;
    $('outboundRecognizeBtn').disabled = !state.outboundImage || !!state.outboundOcr; $('outboundRecognizeBtn').textContent = state.outboundOcr ? '识别完成' : state.outboundOcrRecordId ? '继续查询结果' : '开始识别';
  }
}
const subcontractDirectionName = direction => direction === 'inbound' ? '委外入库' : '委外出库';
function clearSubcontractImage() {
  if (state.subcontractTimer) clearInterval(state.subcontractTimer);
  state.subcontractTimer = null; state.subcontractController = null;
  if (state.subcontractImageUrl) URL.revokeObjectURL(state.subcontractImageUrl);
  state.subcontractImageUrl = null; state.subcontractImage = null; state.subcontractOcr = null; state.subcontractOcrRecordId = null; state.subcontractImageRotation = 0;
  $('subcontractFileInput').value = ''; $('subcontractPreviewImg').removeAttribute('src'); $('subcontractPreviewImg').style.transform = ''; $('subcontractImagePreview').hidden = true;
  $('subcontractRecognizeBtn').disabled = true; $('subcontractRecognizeBtn').textContent = '开始识别'; $('subcontractCancelRecognize').hidden = true;
}
function addSubcontractItem(item = {}) {
  const row = document.createElement('tr');
  row.classList.toggle('low-confidence', !!item.needs_review);
  const quantityName = state.subcontractDirection === 'inbound' ? '入库数量' : '出库数量';
  row.innerHTML = `<td><select class="product" required aria-label="商品"><option value="">请选择商品</option>${state.products.map(p => `<option value="${p.id}">${escape(p.name)}${p.spec ? ' · '+escape(p.spec) : ''} · ${escape(p.sku)}</option>`).join('')}</select><small class="raw-name"></small></td><td><input class="qty" aria-label="${quantityName}" type="number" min="0.01" step="0.01" max="9999999999.99" required></td><td class="unit"></td><td><input class="remark" aria-label="行备注" maxlength="500" placeholder="选填"></td><td><button class="remove-row" type="button" data-action="remove-subcontract-item" aria-label="移除此商品">×</button></td>`;
  row.querySelector('.product').value = item.product_id ?? '';
  row.querySelector('.qty').value = item.qty ?? '';
  row.querySelector('.remark').value = item.remark ?? '';
  row.querySelector('.raw-name').textContent = item.raw_name ? `原图：${item.raw_name}${item.needs_review ? ' · 请核对' : ''}` : '';
  row.querySelector('.unit').textContent = state.products.find(p => p.id === item.product_id)?.unit || item.unit || '—';
  row.querySelector('.product').addEventListener('change', () => { row.querySelector('.unit').textContent = state.products.find(p => p.id === Number(row.querySelector('.product').value))?.unit || '—'; row.classList.remove('low-confidence'); });
  $('subcontractItemRows').append(row); state.subcontractDirty = true; validateSubcontract();
}
function configureSubcontractLabels() {
  const inbound = state.subcontractDirection === 'inbound';
  const name = subcontractDirectionName(state.subcontractDirection);
  $('subcontractEyebrow').textContent = inbound ? 'SUBCONTRACT INBOUND' : 'SUBCONTRACT OUTBOUND';
  $('subcontractUploadTitle').textContent = `点击或拖拽${name}单图片`;
  $('subcontractWarehouseLabel').textContent = inbound ? '入库仓库' : '出库仓库';
  $('subcontractQtyLabel').textContent = inbound ? '入库数量' : '出库数量';
  $('subcontractSubmit').textContent = `生成${name}单`;
  $('subcontractFootnote').textContent = `确认后生成待审核${name}单；暂不自动推送 ERP 或变更库存。`;
  $('subcontractRecentTitle').textContent = `最近${name}单`;
}
function validateSubcontract() {
  const rows = [...$('subcontractItemRows').rows];
  const itemsReady = rows.length > 0 && rows.every(row => {
    const productId = Number(row.querySelector('.product')?.value);
    const quantity = row.querySelector('.qty'), qty = Number(quantity?.value);
    return productId > 0 && Number.isFinite(qty) && qty > 0 && quantity.checkValidity();
  });
  const formReady = Number($('subcontractSupplier').value) > 0 && Number($('subcontractWarehouse').value) > 0 && itemsReady;
  const modeReady = state.subcontractMode === 'manual' || !!state.subcontractOcr;
  $('subcontractSubmit').disabled = state.busy || !formReady || !modeReady;
  return formReady && modeReady;
}
function setSubcontractMode(mode) {
  if (!['image', 'manual'].includes(mode) || state.busy || state.subcontractEdit) return;
  state.subcontractMode = mode;
  renderSubcontractMode();
}
function renderSubcontractMode() {
  const image = state.subcontractMode === 'image', name = subcontractDirectionName(state.subcontractDirection);
  $('subcontractModeImage').classList.toggle('active', image); $('subcontractModeImage').setAttribute('aria-pressed', String(image));
  $('subcontractModeManual').classList.toggle('active', !image); $('subcontractModeManual').setAttribute('aria-pressed', String(!image));
  $('subcontractModes').hidden = !canWrite() || !!state.subcontractEdit; $('subcontractModeHint').hidden = !canWrite(); $('subcontractForm').hidden = !canWrite();
  $('subcontractImageMode').hidden = !image || !canWrite();
  $('subcontractConfidence').hidden = !image || !state.subcontractOcr;
  $('subcontractTitle').textContent = canWrite() ? `${image ? '拍照' : '手动'}创建${name}单` : `${name}单`;
  $('subcontractModeHint').textContent = image ? '上传图片识别商品和数量，再手动选择加工商与仓库。' : '手动选择加工商、仓库和商品数量，无需上传图片。';
  $('subcontractFootnote').textContent = `确认后生成待审核${name}单；${image ? '保留原始图片，' : ''}暂不自动推送 ERP 或变更库存。`;
  $('subcontractCancelEdit').hidden = !state.subcontractEdit;
  if (state.subcontractEdit) {
    $('subcontractTitle').textContent = `修改${name}单 ${state.subcontractEdit.order_no}`;
    $('subcontractSubmit').textContent = '重新提交审核';
    $('subcontractModeHint').textContent = `驳回原因：${state.subcontractEdit.rejection_reason || '—'}`;
    $('subcontractFootnote').textContent = '修改后重新提交原单审核，保留单号、原始图片和处理记录；不推送 ERP、不变更库存。';
  }
  validateSubcontract();
}
function resetSubcontract() {
  state.subcontractEdit = null;
  clearSubcontractImage(); configureSubcontractLabels();
  $('subcontractFeedback').textContent = ''; $('subcontractConfidence').hidden = true; $('subcontractRemark').value = '';
  fillSelect('subcontractSupplier', state.suppliers, '', '请选择加工商');
  fillSelect('subcontractWarehouse', state.warehouses, '', '请选择仓库');
  $('subcontractItemRows').replaceChildren(); addSubcontractItem(); state.subcontractDirty = false;
  renderSubcontractMode();
}
async function refreshSubcontractOrders() {
  state.subcontractOrders = await api(`/api/subcontract/orders?direction=${state.subcontractDirection}`);
  renderSubcontractOrders();
}
function renderSubcontractOrders() {
  const name = subcontractDirectionName(state.subcontractDirection);
  $('subcontractOrderRows').innerHTML = state.subcontractOrders.length ? state.subcontractOrders.map(order => `<tr><td><button type="button" class="subcontract-order-link" data-action="subcontract-detail" data-id="${order.id}">${escape(order.order_no)}</button></td><td>${escape(subcontractDirectionName(order.direction))}</td><td>${escape(nameOf(state.suppliers, order.supplier_id))}</td><td>${escape(nameOf(state.warehouses, order.warehouse_id))}</td><td>${badge(order.status)}</td><td>${escape(timeText(order.created_at))}</td></tr>`).join('') : `<tr><td colspan="6" class="empty-cell">暂无${name}单</td></tr>`;
  $('subcontractOrderTotal').textContent = `显示 ${state.subcontractOrders.length} 张最近${name}单`;
}
async function openSubcontract(direction) {
  const action = direction === 'inbound' ? 'subcontract-inbound' : 'subcontract-outbound';
  if (!requireLogin(action)) return;
  if ($('subcontractDialog').open) return;
  state.subcontractDirection = direction; state.subcontractMode = 'manual';
  await loadMaster(); resetSubcontract(); show('subcontractDialog');
  try { await refreshSubcontractOrders(); } catch (error) { $('subcontractFeedback').textContent = `历史委外单暂未加载：${error.message}`; }
  if (!state.suppliers.length || !state.products.length || !state.warehouses.length) $('subcontractFeedback').textContent = '加工商、商品或仓库主数据尚不完整，请先由管理员同步主数据。';
}
async function openSubcontractDetail(id) {
  if (!state.me) return;
  const requestId = ++subcontractDetailRequestId;
  state.subcontractDetail = null; $('subcontractDetailContent').textContent = '正在加载明细…';
  $('subcontractDetailActions').replaceChildren(); $('subcontractDetailError').textContent = ''; show('subcontractDetailDialog');
  try {
    const order = await api(`/api/subcontract/orders/${id}`);
    if (requestId !== subcontractDetailRequestId) return;
    state.subcontractDetail = order;
    $('subcontractDetailTitle').textContent = `${subcontractDirectionName(order.direction)}单详情`;
    $('subcontractDetailContent').innerHTML = `<div class="detail-meta"><p><small>单据号</small>${escape(order.order_no)}</p><p><small>状态</small>${badge(order.status)}</p><p><small>加工商</small>${escape(nameOf(state.suppliers, order.supplier_id))}</p><p><small>仓库</small>${escape(nameOf(state.warehouses, order.warehouse_id))}</p><p><small>来源</small>${order.source === 'ocr' ? '图片识别' : '手动制单'}</p><p><small>创建时间</small>${escape(timeText(order.created_at))}</p><p><small>审核人</small>${escape(order.audited_by_name || '—')}</p><p><small>审核时间</small>${escape(timeText(order.audited_at))}</p></div><p class="muted">整单备注：${escape(order.remark || '—')}</p><div class="table-wrap"><table class="detail-table"><thead><tr><th>商品 / SKU</th><th>数量</th><th>单位</th><th>行备注</th></tr></thead><tbody>${(order.items || []).map(item => `<tr><td>${escape(item.product_name)}<small>${escape(item.sku)}</small></td><td>${escape(item.qty)}</td><td>${escape(item.unit || '—')}</td><td>${escape(item.remark || '—')}</td></tr>`).join('') || '<tr><td colspan="4">无商品明细</td></tr>'}</tbody></table></div>`;
    if (order.rejection_reason) {
      const reason = document.createElement('p'); reason.className = 'error-text';
      reason.textContent = `${order.status === 'rejected' ? '驳回原因' : '上次驳回原因'}：${order.rejection_reason}`;
      $('subcontractDetailContent').append(reason);
    }
    const history = order.review_history || [];
    if (history.length) {
      const log = document.createElement('details'); log.className = 'settings-group';
      log.innerHTML = `<summary>处理记录（${history.length}）</summary>${history.map(event => `<p>${escape(({reject:'驳回',resubmit:'修改后重新提交',audit:'审核通过'})[event.action] || event.action)} · ${escape(event.username)} · ${escape(timeText(event.at))}${event.reason ? `<br>${escape(event.reason)}` : ''}</p>`).join('')}`;
      $('subcontractDetailContent').append(log);
    }
    if (order.status === 'pending_audit' && canAudit()) $('subcontractDetailActions').innerHTML = '<label>驳回原因<textarea id="subcontractRejectReason" maxlength="1000" placeholder="驳回时必填"></textarea></label><button type="button" class="btn-secondary danger-soft" data-action="subcontract-reject">驳回</button><button type="button" class="btn-primary" data-action="subcontract-audit">确认审核</button>';
    if (order.status === 'rejected' && canWrite() && (state.me.role === 'admin' || order.created_by === state.me.id)) $('subcontractDetailActions').innerHTML = '<button type="button" class="btn-primary" data-action="subcontract-edit">修改并重新提交</button>';
    if (order.status === 'audited') $('subcontractDetailActions').innerHTML = '<button type="button" class="btn-secondary" data-action="subcontract-print">打印 / 保存 PDF</button><button type="button" class="btn-primary" data-action="subcontract-export">导出 Excel</button>';
  } catch (error) { if (requestId === subcontractDetailRequestId) { $('subcontractDetailContent').textContent = ''; $('subcontractDetailError').textContent = error.message; } }
}
async function outputSubcontractOrder(kind, button) {
  const order = state.subcontractDetail;
  if (!order || order.status !== 'audited' || state.busy) return;
  const printing = kind === 'print';
  // Open synchronously during the user's click so the browser does not block an async popup.
  const popup = printing ? window.open('', '_blank', 'width=1100,height=760') : null;
  if (printing && !popup) { $('subcontractDetailError').textContent = '浏览器阻止了打印窗口，请允许弹出窗口后重试。'; return; }
  if (popup) { popup.opener = null; popup.document.body.textContent = '正在加载打印单据…'; }
  const idleText = button.textContent, controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  state.busy = true; $('subcontractDetailError').textContent = '';
  $('subcontractDetailActions').querySelectorAll('button').forEach(b => b.disabled = true);
  button.textContent = printing ? '正在准备打印…' : '正在生成 Excel…';
  try {
    const response = await fetch(`/api/subcontract/orders/${order.id}/${printing ? 'print' : 'export'}`, {
      headers:{Authorization:`Bearer ${getToken()}`}, signal:controller.signal,
    });
    if (!response.ok) {
      let message = `操作失败（${response.status}）`;
      try { const body = await response.json(); message = body?.data?.detail || body?.detail || body?.message || message; } catch { /* 保留状态码。 */ }
      if (response.status === 401) window.dispatchEvent(new Event('session-expired'));
      throw new Error(message);
    }
    if (printing) {
      const html = await response.text(); if (popup.closed) return;
      popup.document.open(); popup.document.write(html); popup.document.close();
      popup.document.getElementById('printDocument')?.addEventListener('click', () => popup.print());
      await popup.document.fonts.ready;
      if (!popup.closed) { popup.focus(); popup.print(); }
    } else {
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const filename = match ? decodeURIComponent(match[1]) : `${subcontractDirectionName(order.direction)}单_${order.order_no}.xlsx`;
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url),1000); toast('委外单 Excel 已导出。');
    }
  } catch (error) {
    if (popup && !popup.closed) popup.close();
    $('subcontractDetailError').textContent = error.name === 'AbortError' ? '生成超时，请重试。' : error.message;
  } finally {
    clearTimeout(timeout); state.busy = false; button.textContent = idleText;
    $('subcontractDetailActions').querySelectorAll('button').forEach(b => b.disabled = false);
  }
}
async function rejectSubcontractOrder() {
  const order = state.subcontractDetail;
  if (!order || order.status !== 'pending_audit' || !canAudit() || state.busy) return;
  const reason = $('subcontractRejectReason').value.trim();
  if (!reason) { $('subcontractDetailError').textContent = '请填写驳回原因。'; $('subcontractRejectReason').focus(); return; }
  if (!confirm(`确认驳回 ${order.order_no}？制单人可修改后重新提交。`)) return;
  state.busy = true; $('subcontractDetailError').textContent = '';
  $('subcontractDetailActions').querySelectorAll('button').forEach(button => button.disabled = true);
  try {
    await api(`/api/subcontract/orders/${order.id}/reject`, { method:'POST', body:{reason, revision:order.revision} });
    toast('委外单已驳回。'); await openSubcontractDetail(order.id);
    try { await refreshSubcontractOrders(); } catch { $('subcontractDetailError').textContent = '驳回已成功，列表暂未刷新。'; }
  } catch (error) { $('subcontractDetailError').textContent = error.message; }
  finally { state.busy = false; $('subcontractDetailActions').querySelectorAll('button').forEach(button => button.disabled = false); }
}
function editSubcontractOrder() {
  const order = state.subcontractDetail;
  if (!order || order.status !== 'rejected' || !canWrite() || state.busy || (state.me.role !== 'admin' && order.created_by !== state.me.id)) return;
  if (state.subcontractDirty && !confirm('修改此单会替换当前未提交的填写内容，继续？')) return;
  $('subcontractDetailDialog').close(); subcontractDetailRequestId++; state.subcontractDetail = null;
  state.subcontractDirection = order.direction; state.subcontractMode = 'manual'; resetSubcontract(); state.subcontractEdit = order;
  fillSelect('subcontractSupplier', state.suppliers, order.supplier_id, '请选择加工商');
  fillSelect('subcontractWarehouse', state.warehouses, order.warehouse_id, '请选择仓库');
  $('subcontractRemark').value = order.remark || '';
  $('subcontractItemRows').replaceChildren(); (order.items || []).forEach(addSubcontractItem);
  state.subcontractDirty = false; renderSubcontractMode(); show('subcontractDialog'); $('subcontractTitle').scrollIntoView();
}
async function auditSubcontractOrder() {
  const order = state.subcontractDetail;
  if (!order || order.status !== 'pending_audit' || !canAudit() || state.busy) return;
  if (!confirm(`确认已核对 ${order.order_no} 的加工商、仓库、商品与数量，执行审核？本次不推送 ERP 或变更库存。`)) return;
  state.busy = true; $('subcontractDetailError').textContent = '';
  $('subcontractDetailActions').querySelectorAll('button').forEach(button => button.disabled = true);
  try {
    await api(`/api/subcontract/orders/${order.id}/audit`, { method: 'POST', body:{revision:order.revision} });
    toast('委外单已审核；未推送 ERP、未变更库存。');
    await openSubcontractDetail(order.id);
    try { await refreshSubcontractOrders(); } catch { $('subcontractDetailError').textContent = '审核已成功，列表暂未刷新，请重新打开委外工作区。'; }
  } catch (error) { $('subcontractDetailError').textContent = error.message; }
  finally { state.busy = false; $('subcontractDetailActions').querySelectorAll('button').forEach(button => button.disabled = false); }
}
function subcontractBody() {
  const supplier_id = Number($('subcontractSupplier').value), warehouse_id = Number($('subcontractWarehouse').value);
  if (!supplier_id) throw new Error('请选择加工商。');
  if (!warehouse_id) throw new Error(`请选择${state.subcontractDirection === 'inbound' ? '入库' : '出库'}仓库。`);
  const items = [...$('subcontractItemRows').rows].map((row, index) => {
    const product_id = Number(row.querySelector('.product').value), qty = Number(row.querySelector('.qty').value);
    if (!product_id || !Number.isFinite(qty) || qty <= 0) throw new Error(`第 ${index+1} 行商品或数量无效。`);
    return { product_id, qty, remark: row.querySelector('.remark').value.trim() || null };
  });
  if (!items.length) throw new Error('请至少添加一行商品。');
  return { source: state.subcontractMode === 'image' ? 'ocr' : 'manual', direction: state.subcontractDirection, supplier_id, warehouse_id, remark: $('subcontractRemark').value.trim() || null, items };
}
$('subcontractForm').addEventListener('input', () => { state.subcontractDirty = true; validateSubcontract(); });
$('subcontractForm').addEventListener('change', () => { state.subcontractDirty = true; validateSubcontract(); });
$('subcontractForm').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy || !canWrite()) return;
  try {
    if (!validateSubcontract()) throw new Error(state.subcontractMode === 'image' ? '请先完成图片识别并填写完整。' : '请完整选择加工商、仓库、商品和数量。');
    const body = subcontractBody(), name = subcontractDirectionName(state.subcontractDirection);
    const editing = state.subcontractEdit;
    state.busy = true; $('subcontractSubmit').disabled = true; $('subcontractSubmit').textContent = editing ? '正在重新提交…' : '正在生成…'; $('subcontractFeedback').textContent = '';
    const {source, direction, ...editable} = body;
    const result = editing
      ? await api(`/api/subcontract/orders/${editing.id}/resubmit`, { method:'POST', body:{...editable, revision:editing.revision} })
      : state.subcontractMode === 'image'
      ? await api(`/api/subcontract/ocr/${state.subcontractOcr.ocr_record_id}/commit`, { method: 'POST', body })
      : await api('/api/subcontract/orders', { method: 'POST', body });
    const order = result.order || result; sound(); toast(`${editing ? '已重新提交' : '已生成'}${name}单 ${order.order_no} · 待审核`);
    state.subcontractDirty = false; resetSubcontract();
    try { await refreshSubcontractOrders(); } catch { $('subcontractFeedback').textContent = '提交已成功，列表暂未刷新，请重新打开工作区。'; }
  } catch (error) { $('subcontractFeedback').textContent = error.message; }
  finally { state.busy = false; renderSubcontractMode(); $('subcontractSubmit').textContent = state.subcontractEdit ? '重新提交审核' : `生成${subcontractDirectionName(state.subcontractDirection)}单`; validateSubcontract(); }
});
function chooseSubcontractImage(file) {
  if (state.busy) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast('请选择 JPG、PNG 或 WebP 图片。'); return; }
  if (!file.size || file.size > 10*1024*1024) { toast('图片需小于 10 MB，且不能为空。'); return; }
  clearSubcontractImage(); state.subcontractImage = file; state.subcontractImageUrl = URL.createObjectURL(file); state.subcontractDirty = true;
  $('subcontractPreviewImg').src = state.subcontractImageUrl; $('subcontractImageFilename').textContent = file.name; $('subcontractRotationStatus').textContent = '方向：原图';
  $('subcontractImageStatus').textContent = '图片已选择，等待开始识别'; $('subcontractImagePreview').hidden = false; $('subcontractRecognizeBtn').disabled = false; $('subcontractSubmit').disabled = true;
}
function rotateSubcontractImage(delta) {
  if (!state.subcontractImage) return;
  state.subcontractImageRotation = (state.subcontractImageRotation + delta + 360) % 360;
  $('subcontractPreviewImg').style.transform = `rotate(${state.subcontractImageRotation}deg)`;
  $('subcontractRotationStatus').textContent = state.subcontractImageRotation ? `方向：已旋转 ${state.subcontractImageRotation}°` : '方向：原图';
  state.subcontractOcr = null; state.subcontractOcrRecordId = null; $('subcontractSubmit').disabled = true; $('subcontractConfidence').hidden = true;
  $('subcontractImageStatus').textContent = '方向已调整，请重新识别'; $('subcontractRecognizeBtn').textContent = '开始识别'; $('subcontractRecognizeBtn').disabled = false; state.subcontractDirty = true;
}
async function subcontractImageForRecognition() {
  if (!state.subcontractImageRotation) return state.subcontractImage;
  const bitmap = await createImageBitmap(state.subcontractImage), swap = state.subcontractImageRotation === 90 || state.subcontractImageRotation === 270;
  const canvas = document.createElement('canvas'); canvas.width = swap ? bitmap.height : bitmap.width; canvas.height = swap ? bitmap.width : bitmap.height;
  const context = canvas.getContext('2d'); context.translate(canvas.width/2, canvas.height/2); context.rotate(state.subcontractImageRotation*Math.PI/180); context.drawImage(bitmap,-bitmap.width/2,-bitmap.height/2); bitmap.close?.();
  const type = ['image/jpeg','image/png','image/webp'].includes(state.subcontractImage.type) ? state.subcontractImage.type : 'image/jpeg';
  const blob = await new Promise((resolve,reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('图片旋转失败，请重新选择图片。')), type, .95));
  return new File([blob], state.subcontractImage.name, { type, lastModified: Date.now() });
}
$('subcontractFileInput').addEventListener('change', event => { if (event.target.files[0]) chooseSubcontractImage(event.target.files[0]); });
['dragover','dragenter'].forEach(type => $('subcontractImageDrop').addEventListener(type, event => { event.preventDefault(); $('subcontractImageDrop').classList.add('drag'); }));
['drop','dragleave'].forEach(type => $('subcontractImageDrop').addEventListener(type, event => { event.preventDefault(); $('subcontractImageDrop').classList.remove('drag'); if (type === 'drop' && event.dataTransfer.files[0]) chooseSubcontractImage(event.dataTransfer.files[0]); }));
async function recognizeSubcontract() {
  if (!state.subcontractImage || state.busy || !canWrite()) return;
  const controller = new AbortController(); state.subcontractController = controller; state.busy = true; $('subcontractFileInput').disabled = true; $('subcontractRecognizeBtn').disabled = true; $('subcontractCancelRecognize').hidden = false;
  const started = Date.now(); const updateWait = () => { $('subcontractImageStatus').textContent = `后台识别中… 已等待 ${Math.floor((Date.now()-started)/1000)} 秒，通常需要 1–2 分钟`; };
  updateWait(); state.subcontractTimer = setInterval(updateWait, 1000);
  try {
    let result;
    if (state.subcontractOcrRecordId) result = await api(`/api/subcontract/ocr/${state.subcontractOcrRecordId}`, { signal: controller.signal });
    else {
      const form = new FormData(); form.append('direction', state.subcontractDirection); form.append('file', await subcontractImageForRecognition());
      result = await api('/api/subcontract/ocr/upload', { method: 'POST', body: form, timeout: 20000, signal: controller.signal }); state.subcontractOcrRecordId = result.ocr_record_id;
    }
    const recordId = state.subcontractOcrRecordId;
    while (result.action === 'processing' && Date.now()-started < 180000) {
      await new Promise((resolve,reject) => { const timer=setTimeout(resolve,2000); controller.signal.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));},{once:true}); });
      result = await api(`/api/subcontract/ocr/${recordId}`, { timeout: 25000, signal: controller.signal });
    }
    if (result.action === 'processing') throw new Error('后台仍在识别，可点击“继续查询结果”。');
    if (result.action !== 'recognized' || !result.recognized) throw new Error(result.action === 'needs_configuration' ? '图片已保存，但模型尚未配置。请联系管理员。' : (result.message || '委外单识别失败，请检查 API 设置后重试。'));
    state.subcontractOcr = result; const draft = result.recognized;
    $('subcontractItemRows').replaceChildren(); draft.items.forEach(item => addSubcontractItem(item)); if (!draft.items.length) addSubcontractItem();
    $('subcontractConfidence').textContent = `整体置信度 ${Math.round((draft.overall_confidence || 0)*100)}% · 请手动选择加工商和仓库，并核对黄色商品及数量。`; $('subcontractConfidence').hidden = false;
    $('subcontractImageStatus').textContent = `识别完成 · 记录 #${result.ocr_record_id}`; validateSubcontract();
  } catch (error) { $('subcontractImageStatus').textContent = error.name === 'AbortError' ? '已停止等待，识别仍可能在后台进行。可点击“继续查询结果”。' : error.message; }
  finally {
    if (state.subcontractTimer) clearInterval(state.subcontractTimer); state.subcontractTimer = null; state.subcontractController = null; state.busy = false; $('subcontractCancelRecognize').hidden = true; $('subcontractFileInput').disabled = false;
    $('subcontractRecognizeBtn').disabled = !state.subcontractImage || !!state.subcontractOcr; $('subcontractRecognizeBtn').textContent = state.subcontractOcr ? '识别完成' : state.subcontractOcrRecordId ? '继续查询结果' : '开始识别'; validateSubcontract();
  }
}
function renderOrders() {
  const search = $('orderSearch').value.trim().toLowerCase();
  const orders = state.orders.filter(o => (state.filter === 'all' || o.status === state.filter) && `${o.order_no} ${nameOf(state.suppliers,o.supplier_id)}`.toLowerCase().includes(search));
  $('orderRows').innerHTML = orders.length ? orders.map(o => `<tr><td><b>${escape(o.order_no)}</b></td><td>${escape(nameOf(state.suppliers,o.supplier_id))}<small>${escape(nameOf(state.warehouses,o.warehouse_id))}</small></td><td>${o.source === 'ocr' ? '图片识别' : '手动'}</td><td>${badge(o.status)}</td><td>${erpBadge(o)}</td><td>${escape(timeText(o.created_at))}</td><td><button class="link-btn" data-action="detail" data-id="${o.id}">查看详情 ↗</button></td></tr>`).join('') : '<tr><td colspan="7" class="empty-cell">暂无符合条件的单据</td></tr>';
  $('orderTotal').textContent = `共 ${orders.length} 张单据`;
}
$('orderSearch').addEventListener('input', renderOrders);
$('returnFile').addEventListener('change', event => { if (event.target.files[0]) previewReturnFile(event.target.files[0]); });
$('returnVoucherBatchFile').addEventListener('change', event => { if (event.target.files[0]) importVoucherBatchFile(event.target.files[0]); });
Object.values(returnMappingIds).forEach(id => $(id).addEventListener('change', () => validateReturnMapping(true)));
$('returnSearch').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); refreshReturnRecords().catch(error => $('returnImportError').textContent = error.message); } });
$('waybillScanInput').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); document.querySelector('[data-action="scan-waybill"]').click(); } });
$('orderFilters').addEventListener('click', e => {
  const button = e.target.closest('[data-filter]'); if (!button) return;
  state.filter = button.dataset.filter;
  $('orderFilters').querySelectorAll('button').forEach(b => { b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', String(b === button)); }); renderOrders();
});
async function openDetail(id) {
  const requestId = ++detailId;
  state.detail = null; $('detailContent').textContent = '正在加载明细…'; $('detailActions').replaceChildren(); $('detailError').textContent = ''; show('detailDialog');
  try {
    const order = await api(`/api/purchase/orders/${id}`);
    if (requestId !== detailId) return;
    state.detail = order;
    $('detailContent').innerHTML = `<div class="detail-meta"><p><small>单据号</small>${escape(order.order_no)}</p><p><small>状态</small>${badge(order.status)}</p><p><small>ERP 状态</small>${erpBadge(order)}</p><p><small>来源</small>${order.source === 'ocr' ? '图片识别' : '手动制单'}</p><p><small>供应商</small>${escape(nameOf(state.suppliers,order.supplier_id))}</p><p><small>仓库</small>${escape(nameOf(state.warehouses,order.warehouse_id))}</p><p><small>创建时间</small>${escape(timeText(order.created_at))}</p><p><small>推送次数</small>${escape(order.erp_push_attempts || 0)}</p><p><small>最近推送</small>${escape(timeText(order.erp_pushed_at))}</p></div>${order.erp_last_error ? `<p class="error-text">${escape(order.erp_last_error)}</p>` : ''}<div class="table-wrap"><table class="detail-table"><thead><tr><th>商品 / SKU</th><th>数量</th><th>单位</th><th>单价</th></tr></thead><tbody>${(order.items || []).map(i => `<tr><td>${escape(i.product_name || nameOf(state.products,i.product_id))}<small>${escape(i.sku)}</small></td><td>${escape(i.qty ?? '—')}</td><td>${escape(i.unit || '—')}</td><td>${escape(i.price ?? '—')}</td></tr>`).join('') || '<tr><td colspan="4">无商品明细</td></tr>'}</tbody></table></div>`;
    const actions = [];
    if (order.status === 'pending_audit' && canWrite()) actions.push('<button class="btn-secondary" data-action="edit">修改单据</button>');
    if (order.status === 'pending_audit' && canAudit()) actions.push('<button class="btn-primary" data-action="audit">确认审核</button>');
    if (order.status === 'audited' && canAudit() && !order.erp_push_attempts) actions.push('<button class="btn-secondary" data-action="return">反审核，退回修改</button>');
    if (order.status === 'audited' && canWrite() && order.erp_push_status !== 'succeeded') actions.push(`<button class="btn-primary" data-action="push-erp">${order.erp_push_status === 'failed' ? '重试推送 ERP' : '推送 ERP'}</button>`);
    $('detailActions').innerHTML = actions.join('');
  } catch (e) { if (requestId === detailId) { $('detailContent').textContent = ''; $('detailError').textContent = e.message; } }
}
async function editOrder() {
  const order = state.detail; if (!order || order.status !== 'pending_audit' || !canWrite()) return;
  await loadMaster(); resetPurchase(); state.edit = order;
  $('purchaseTitle').textContent = '修改待审核采购单'; $('purchaseModes').hidden = true;
  fillSelect('supplierSelect', state.suppliers, order.supplier_id, '请选择供应商'); fillSelect('warehouseSelect', state.warehouses, order.warehouse_id, '请选择仓库');
  $('itemRows').replaceChildren(); (order.items || []).forEach(item => addItem(item)); if (!order.items?.length) addItem();
  state.dirty = false; updateMode(); show('purchaseDialog');
}
async function changeOrderStatus(action) {
  const order = state.detail; if (!order || state.busy || !canAudit()) return;
  if (!confirm(action === 'audit' ? `确认已核对 ${order.order_no} 的供应商、商品与数量，执行审核？` : `将 ${order.order_no} 退回待审核状态？`)) return;
  state.busy = true; $('detailActions').querySelectorAll('button').forEach(b => b.disabled = true);
  try { await api(`/api/purchase/orders/${order.id}/${action}`, { method: 'POST' }); toast(action === 'audit' ? '单据已审核' : '已退回待审核，可修改'); await refreshOrders(); await openDetail(order.id); }
  catch (e) { $('detailError').textContent = e.message; }
  finally { state.busy = false; $('detailActions').querySelectorAll('button').forEach(b => b.disabled = false); }
}
async function pushOrderToErp() {
  const order = state.detail;
  if (!order || order.status !== 'audited' || !canWrite() || state.busy) return;
  const retrying = order.erp_push_status === 'failed';
  if (!confirm(`${retrying ? '重新' : ''}推送 ${order.order_no} 到 ERP？系统会使用单据号防止重复建单。`)) return;
  state.busy = true; $('detailError').textContent = '';
  $('detailActions').querySelectorAll('button').forEach(button => button.disabled = true);
  try {
    const result = await api(`/api/purchase/orders/${order.id}/push-erp`, { method: 'POST', timeout: 30000 });
    toast(result.reused ? `ERP 已存在该单据：${result.erp_order_id}` : `推送成功：${result.erp_order_id}`);
    await refreshOrders(); await openDetail(order.id);
  } catch (error) {
    await refreshOrders(); await openDetail(order.id); $('detailError').textContent = error.message;
  } finally {
    state.busy = false; $('detailActions').querySelectorAll('button').forEach(button => button.disabled = false);
  }
}
function setApiKeyEditing(editing) {
  const readonly = state.me?.role !== 'admin';
  if (!state.apiKeyConfigured) editing = true;
  state.apiKeyEditing = editing;
  $('aiApiKey').disabled = readonly || !editing;
  $('aiApiKey').classList.toggle('key-editing', editing && !readonly);
  $('editApiKeyBtn').hidden = readonly || !state.apiKeyConfigured;
  $('editApiKeyBtn').disabled = readonly;
  $('editApiKeyBtn').textContent = editing ? '取消更换' : '更换密钥';
  if (!editing) $('aiApiKey').value = '';
  $('aiApiKey').placeholder = editing ? '在这里输入新的 API Key' : `已配置 ${state.apiKeyHint}，点击右侧按钮更换`;
  $('apiKeyHelp').textContent = editing ? '编辑模式已开启：输入新密钥后点击下方“保存设置”。' : `当前密钥：${state.apiKeyHint}。点击“更换密钥”后输入新值。`;
}
function updateErpTypeUi(applyDefault = false) {
  const isWdt = $('erpType').value === 'wdt_qyb';
  $('erpSidField').hidden = !isWdt;
  $('erpTokenField').hidden = isWdt;
  $('erpHelp').textContent = isWdt
    ? '旺店通企业版使用 SID、AppKey、AppSecret 签名；建议先使用测试环境联调。'
    : '管家婆 NGP 需要 API 地址、AppKey、AppSecret 和授权 TOKEN。';
  $('erpPortalLink').href = isWdt ? 'https://open.wangdian.cn/' : 'https://ngpopen.wsgjp.com/#/';
  $('erpPortalLink').textContent = isWdt ? '打开旺店通官方开放平台 ↗' : '打开 NGP 官方开放平台 ↗';
  $('erpSyncBtn').hidden = !isWdt;
  $('erpSyncBtn').disabled = !state.erpConfigured || state.me?.role !== 'admin';
  if (applyDefault && isWdt && (!$('erpBaseUrl').value || $('erpBaseUrl').value.includes('wsgjp.com'))) {
    $('erpBaseUrl').value = 'https://sandbox.wangdian.cn/openapi2';
  }
}
$('erpType').addEventListener('change', () => updateErpTypeUi(true));
async function openSettings() {
  if (!requireLogin('settings')) return;
  document.querySelectorAll('#settingsDialog details[open]').forEach(group => group.removeAttribute('open'));
  $('settingsError').textContent = ''; $('settingsRetry').hidden = true; $('settingsSubmit').disabled = true; $('autoAudit').disabled = true; $('aiBaseUrl').disabled = true; $('aiVisionModel').disabled = true; $('aiApiKey').disabled = true; $('editApiKeyBtn').disabled = true; show('settingsDialog');
  ['erpType','erpBaseUrl','erpSid','erpAppKey','erpAppSecret','erpToken'].forEach(id => $(id).disabled = true);
  try {
    const setting = await api('/api/settings'); $('autoAudit').checked = !!setting.auto_audit_enabled;
    $('aiBaseUrl').value = setting.ai_base_url || ''; $('aiVisionModel').value = setting.ai_vision_model || ''; $('aiApiKey').value = '';
    state.apiKeyConfigured = !!setting.ai_api_key_configured; state.apiKeyHint = setting.ai_api_key_hint || '••••'; setApiKeyEditing(false);
    $('aiStatus').textContent = setting.ai_configured ? `● API 已配置 · ${setting.ai_vision_model}` : '○ API 尚未完整配置';
    $('erpType').value = setting.erp_type || 'wdt_qyb'; $('erpBaseUrl').value = setting.erp_base_url || '';
    state.erpConfigured = !!setting.erp_configured;
    $('erpSid').value = ''; $('erpAppKey').value = ''; $('erpAppSecret').value = ''; $('erpToken').value = ''; updateErpTypeUi(false);
    $('erpSid').placeholder = setting.erp_sid_configured ? `已配置 ${setting.erp_sid_hint}，留空则保留` : '请输入 SID（卖家账号）';
    $('erpAppKey').placeholder = setting.erp_appkey_configured ? `已配置 ${setting.erp_appkey_hint}，留空则保留` : '请输入 AppKey';
    $('erpAppSecret').placeholder = setting.erp_appsecret_configured ? `已配置 ${setting.erp_appsecret_hint}，留空则保留` : '请输入 AppSecret';
    $('erpToken').placeholder = setting.erp_token_configured ? `已配置 ${setting.erp_token_hint}，留空则保留` : '请输入 ERP 授权 TOKEN';
    $('erpStatus').textContent = setting.erp_configured ? '● ERP 凭证已完整配置 · 可推送采购单' : '○ ERP 尚未完整配置';
    $('settingsSummary').textContent = `当前自动审核阈值：${Math.round(Number(setting.audit_threshold)*100)}%。${state.me.role === 'admin' ? '' : '仅管理员可修改设置。'}`;
    const readonly = state.me.role !== 'admin'; $('settingsSubmit').disabled = readonly; $('autoAudit').disabled = readonly; $('aiBaseUrl').disabled = readonly; $('aiVisionModel').disabled = readonly;
    ['erpType','erpBaseUrl','erpSid','erpAppKey','erpAppSecret','erpToken'].forEach(id => $(id).disabled = readonly);
    if (readonly) setApiKeyEditing(false);
    $('accountSection').hidden = !(state.me.is_master && state.me.role === 'admin');
    if (! $('accountSection').hidden) await loadAccounts();
  } catch (e) { $('settingsError').textContent = `${e.message} 请恢复服务后点击“重新加载”。`; $('settingsRetry').hidden = false; }
}
$('settingsForm').addEventListener('submit', async e => {
  e.preventDefault(); if (state.busy || state.me?.role !== 'admin') return;
  const newKey = $('aiApiKey').value.trim();
  if (state.apiKeyEditing && !newKey) { $('settingsError').textContent = '请输入新的 API Key，或点击“取消更换”。'; $('aiApiKey').focus(); return; }
  state.busy = true; $('settingsSubmit').disabled = true;
  try {
    const body = {
      auto_audit_enabled: $('autoAudit').checked,
      ai_base_url: $('aiBaseUrl').value.trim(), ai_vision_model: $('aiVisionModel').value.trim(),
      erp_type: $('erpType').value, erp_base_url: $('erpBaseUrl').value.trim(),
      erp_sid: $('erpSid').value.trim(), erp_appkey: $('erpAppKey').value.trim(), erp_appsecret: $('erpAppSecret').value.trim(), erp_token: $('erpToken').value.trim(),
    };
    const keyChanged = !!newKey; if (keyChanged) body.ai_api_key = newKey;
    const saved = await api('/api/settings', { method: 'PUT', body });
    state.apiKeyConfigured = !!saved.ai_api_key_configured; state.apiKeyHint = saved.ai_api_key_hint || '••••'; setApiKeyEditing(false);
    $('apiKeyHelp').textContent = keyChanged ? `API Key 已更新为 ${saved.ai_api_key_hint || '••••'}。` : `当前密钥保持为 ${saved.ai_api_key_hint || '••••'}。`;
    $('aiStatus').textContent = saved.ai_configured ? `● API 已配置 · ${saved.ai_vision_model}` : '○ API 尚未完整配置';
    state.erpConfigured = !!saved.erp_configured;
    $('erpSid').value = ''; $('erpAppKey').value = ''; $('erpAppSecret').value = ''; $('erpToken').value = ''; updateErpTypeUi(false);
    $('erpSid').placeholder = saved.erp_sid_configured ? `已配置 ${saved.erp_sid_hint}，留空则保留` : '请输入 SID（卖家账号）';
    $('erpAppKey').placeholder = saved.erp_appkey_configured ? `已配置 ${saved.erp_appkey_hint}，留空则保留` : '请输入 AppKey';
    $('erpAppSecret').placeholder = saved.erp_appsecret_configured ? `已配置 ${saved.erp_appsecret_hint}，留空则保留` : '请输入 AppSecret';
    $('erpToken').placeholder = saved.erp_token_configured ? `已配置 ${saved.erp_token_hint}，留空则保留` : '请输入 ERP 授权 TOKEN';
    $('erpStatus').textContent = saved.erp_configured ? '● ERP 凭证已完整配置 · 可推送采购单' : '○ ERP 尚未完整配置';
    toast(keyChanged ? `API Key 已更新为 ${saved.ai_api_key_hint || '新密钥'}` : '设置已保存，API Key 未改变');
  }
  catch (error) { $('settingsError').textContent = error.message; }
  finally { state.busy = false; $('settingsSubmit').disabled = state.me?.role !== 'admin'; }
});

async function syncErpMaster(button) {
  if (state.busy || !state.erpConfigured || state.me?.role !== 'admin') return;
  state.busy = true; button.disabled = true; $('settingsError').textContent = '';
  try {
    const result = await api('/api/sync/erp', { method: 'POST', timeout: 60000 });
    const summary = ['suppliers','warehouses','products'].map(key => {
      const value = result[key] || {}; return `${key === 'suppliers' ? '供应商' : key === 'warehouses' ? '仓库' : '商品'}新增 ${value.created || 0} / 更新 ${value.updated || 0}`;
    }).join('，');
    await loadMaster(); toast(`主数据同步完成：${summary}`);
  } catch (error) { $('settingsError').textContent = error.message; }
  finally { state.busy = false; button.disabled = !state.erpConfigured; }
}

function accountRoleOptions(selected) {
  return [['operator','制单员'],['auditor','审核员'],['viewer','只读']].map(([value,label]) => `<option value="${value}"${value === selected ? ' selected' : ''}>${label}</option>`).join('');
}
function renderAccounts() {
  $('accountList').innerHTML = state.accounts.map(account => `<div class="account-row${account.is_active ? '' : ' inactive'}" data-account-id="${account.id}"><div><b>${escape(account.username)}</b><small>${account.is_master ? '主账号' : roleName(account.role)} · ${account.is_active ? '启用' : '已停用'}</small></div>${account.is_master ? '<span class="account-master">主账号不可修改</span>' : `<select class="account-role" aria-label="${escape(account.username)}的权限">${accountRoleOptions(account.role)}</select><button class="btn-secondary" data-action="save-account" data-id="${account.id}">保存权限</button><button class="btn-secondary${account.is_active ? ' danger-soft' : ''}" data-action="toggle-account" data-id="${account.id}" data-active="${account.is_active}">${account.is_active ? '停用' : '启用'}</button>`}</div>`).join('') || '<p class="hint">暂无子账号</p>';
}
async function loadAccounts() {
  state.accounts = await api('/api/accounts'); renderAccounts();
}
$('accountForm').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy || !state.me?.is_master) return;
  state.busy = true; $('accountCreate').disabled = true; $('accountError').textContent = '';
  try {
    await api('/api/accounts', { method: 'POST', body: { username: $('accountUsername').value.trim(), password: $('accountPassword').value, role: $('accountRole').value } });
    $('accountUsername').value = ''; $('accountPassword').value = ''; await loadAccounts(); toast('子账号已创建');
  } catch (error) { $('accountError').textContent = error.message; }
  finally { state.busy = false; $('accountCreate').disabled = false; }
});
async function updateSubaccount(button, toggle) {
  const account = state.accounts.find(row => row.id === Number(button.dataset.id)); if (!account) return;
  if (toggle && account.is_active && !confirm(`停用 ${account.username} 后，该账号将无法登录。继续？`)) return;
  const row = button.closest('.account-row');
  const body = toggle ? { is_active: !account.is_active } : { role: row.querySelector('.account-role').value };
  state.busy = true; $('accountError').textContent = '';
  try { await api(`/api/accounts/${account.id}`, { method: 'PUT', body }); await loadAccounts(); toast(toggle ? (account.is_active ? '账号已停用' : '账号已启用') : '权限已保存'); }
  catch (error) { $('accountError').textContent = error.message; }
  finally { state.busy = false; }
}
async function act(action, button) {
  if (state.busy && !['theme', 'cancel-recognition', 'outbound-cancel-recognition', 'subcontract-cancel-recognition', 'cancel-return-import'].includes(action)) { toast('操作正在进行，请稍候。'); return; }
  switch (action) {
    case 'close': closeDialog(button.closest('dialog')); break;
    case 'home': setHomeExpanded(false); break;
    case 'reveal-home': setHomeExpanded(true); button.blur(); break;
    case 'account':
      if (state.me) { clearSession(); toast('已退出登录'); }
      else { afterLogin = null; show('loginDialog'); } break;
    case 'purchase': await openPurchase(); break;
    case 'orders':
      if (!requireLogin('orders')) return;
      await loadMaster(); show('ordersDialog'); await refreshOrders(); break;
    case 'settings': await openSettings(); break;
    case 'retry-settings': await openSettings(); break;
    case 'edit-api-key':
      $('settingsError').textContent = ''; setApiKeyEditing(!state.apiKeyEditing); if (state.apiKeyEditing) $('aiApiKey').focus(); break;
    case 'sync-erp': await syncErpMaster(button); break;
    case 'save-account': await updateSubaccount(button, false); break;
    case 'toggle-account': await updateSubaccount(button, true); break;
    case 'cancel-recognition': state.ocrController?.abort(); break;
    case 'outbound': await openOutbound(); break;
    case 'outbound-recognize': await recognizeOutbound(); break;
    case 'outbound-cancel-recognition': state.outboundController?.abort(); break;
    case 'outbound-clear-image': resetOutbound(); break;
    case 'outbound-rotate-left': rotateOutboundImage(-90); break;
    case 'outbound-rotate-right': rotateOutboundImage(90); break;
    case 'add-outbound-item': addOutboundItem(); break;
    case 'remove-outbound-item': button.closest('tr').remove(); state.outboundDirty = true; break;
    case 'subcontract-inbound': await openSubcontract('inbound'); break;
    case 'subcontract-outbound': await openSubcontract('outbound'); break;
    case 'subcontract-detail': await openSubcontractDetail(Number(button.dataset.id)); break;
    case 'subcontract-audit': await auditSubcontractOrder(); break;
    case 'subcontract-reject': await rejectSubcontractOrder(); break;
    case 'subcontract-edit': editSubcontractOrder(); break;
    case 'subcontract-export': await outputSubcontractOrder('export', button); break;
    case 'subcontract-print': await outputSubcontractOrder('print', button); break;
    case 'subcontract-cancel-edit': if (!state.subcontractDirty || confirm('取消后丢弃本次修改，原单仍为已驳回。继续？')) resetSubcontract(); break;
    case 'subcontract-mode-image': setSubcontractMode('image'); break;
    case 'subcontract-mode-manual': setSubcontractMode('manual'); break;
    case 'subcontract-recognize': await recognizeSubcontract(); break;
    case 'subcontract-cancel-recognition': state.subcontractController?.abort(); break;
    case 'subcontract-clear-image': resetSubcontract(); break;
    case 'subcontract-rotate-left': rotateSubcontractImage(-90); break;
    case 'subcontract-rotate-right': rotateSubcontractImage(90); break;
    case 'add-subcontract-item': addSubcontractItem(); break;
    case 'remove-subcontract-item': button.closest('tr').remove(); state.subcontractDirty = true; validateSubcontract(); break;
    case 'returns': await openReturns(); break;
    case 'toggle-return-database': await toggleReturnDatabase(button); break;
    case 'scan-waybill': await scanWaybill(button); break;
    case 'select-return-sheet': selectReturnSheet(button); break;
    case 'remove-scanned-waybill': await removeScannedWaybill(button); break;
    case 'clear-scanned-waybills': clearScannedWaybills(); $('waybillScanInput').focus(); break;
    case 'export-return-vouchers': await exportReturnVouchers(button); break;
    case 'print-waybill': printWaybill(); break;
    case 'commit-returns': await commitReturnImport(); break;
    case 'cancel-return-import': await cancelReturnImport(button); break;
    case 'refresh-returns': await refreshReturnRecords(); break;
    case 'add-item': addItem(); break;
    case 'remove-item': button.closest('tr').remove(); state.dirty = true; break;
    case 'mode-image': case 'mode-manual': {
      const mode = action === 'mode-image' ? 'image' : 'manual'; if (state.mode === mode) return;
      if (state.dirty && !confirm('切换方式会清空本次未提交内容，继续？')) return;
      resetPurchase(mode); break;
    }
    case 'clear-image': clearImage(); $('itemRows').replaceChildren(); addItem(); $('confidenceNote').hidden = true; updateMode(); break;
    case 'rotate-left': rotateImage(-90); break;
    case 'rotate-right': rotateImage(90); break;
    case 'recognize': await recognize(); break;
    case 'refresh-orders': await loadMaster(); await refreshOrders(); break;
    case 'detail': await openDetail(Number(button.dataset.id)); break;
    case 'edit': await editOrder(); break;
    case 'audit': case 'return': await changeOrderStatus(action); break;
    case 'push-erp': await pushOrderToErp(); break;
  }
}
document.addEventListener('click', e => {
  const button = e.target.closest('[data-action]'); if (!button) return;
  act(button.dataset.action, button).catch(error => toast(error.message));
});
const savedTheme = localStorage.getItem('prioritize-theme');
function setupBubbleInteraction() {
  const tile = document.querySelector('.hero-tile'), bubble = tile?.querySelector('.hero-bubble'), interaction = tile?.querySelector('.bubble-interaction');
  if (!tile || !bubble || !interaction) return;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let clickAnimation = null;
  const resetPointer = () => {
    interaction.style.setProperty('--bubble-x', '0px'); interaction.style.setProperty('--bubble-y', '0px');
    interaction.style.setProperty('--bubble-tilt', '0deg'); interaction.style.setProperty('--bubble-hover-scale', '1');
  };
  tile.addEventListener('pointermove', event => {
    if (reducedMotion.matches || event.pointerType === 'touch') return;
    const box = tile.getBoundingClientRect();
    const x = Math.max(-1, Math.min(1, (event.clientX - box.x - box.width / 2) / (box.width / 2)));
    const y = Math.max(-1, Math.min(1, (event.clientY - box.y - box.height / 2) / (box.height / 2)));
    interaction.style.setProperty('--bubble-x', `${x * 9}px`); interaction.style.setProperty('--bubble-y', `${y * 8}px`);
    interaction.style.setProperty('--bubble-tilt', `${x * 9}deg`); interaction.style.setProperty('--bubble-hover-scale', '1.04');
  });
  tile.addEventListener('pointerleave', resetPointer);
  tile.addEventListener('pointercancel', resetPointer);
  // 点击和键盘激活都触发；不拦截原有 home 点击事件。
  tile.addEventListener('click', () => {
    clickAnimation?.cancel(); clickAnimation = null;
    if (reducedMotion.matches || !bubble.animate) return;
    const resting = getComputedStyle(bubble);
    const start = { transform: resting.transform, borderRadius: resting.borderRadius };
    const animation = bubble.animate([
      { ...start, offset: 0 },
      { transform: 'rotate(-15deg) scale(1.58,.48) skewX(-12deg)', borderRadius: '27% 73% 32% 68% / 60% 30% 70% 40%', offset: .2 },
      { transform: 'rotate(12deg) scale(.68,1.38) skewX(8deg)', borderRadius: '66% 34% 70% 30% / 30% 68% 32% 70%', offset: .45 },
      { transform: 'rotate(-5deg) scale(1.21,.82)', borderRadius: '35% 65% 43% 57% / 59% 36% 64% 41%', offset: .72 },
      { ...start, offset: 1 },
    ], { duration: 850, easing: 'cubic-bezier(.25,.65,.3,1)' });
    clickAnimation = animation;
    animation.finished.then(() => { if (clickAnimation === animation) clickAnimation = null; }).catch(() => {});
  });
  reducedMotion.addEventListener('change', () => { resetPointer(); if (reducedMotion.matches) { clickAnimation?.cancel(); clickAnimation = null; } });
}
setupBubbleInteraction();
document.documentElement.dataset.theme = savedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
$('themeToggle').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme; localStorage.setItem('prioritize-theme', theme);
});
$('memoText').addEventListener('blur', () => { if (state.me) localStorage.setItem(`zhicang-memo-${state.me.id}`, $('memoText').textContent.trim()); });
async function bootstrap() {
  setHomeExpanded(false);
  updateAccount(); renderDashboard();
  if (location.protocol === 'file:') { toast('请通过后端地址 /console-v2/ 打开此页面。'); return; }
  if (!hasSession()) return;
  try { state.me = await api('/api/auth/me'); updateAccount(); await loadMaster(); await refreshOrders(); }
  catch (e) { toast(e.message); }
}
bootstrap();

// 用模拟接口验证真实前端交互，不向正式数据库创建任何单据。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');

function documentOutput(order, kind) {
  const inbound = order.direction === 'inbound';
  const doc = {title:inbound?'委外入库单':'委外出库单',order_no:order.order_no,
    warehouse_label:inbound?'入库仓库':'出库仓库',qty_label:inbound?'入库数量':'出库数量',
    supplier_name:'测试加工商',warehouse_name:'测试仓库',created_by_name:'admin',
    created_at:'2026-09-17 21:00:00',audited_by_name:'admin',audited_at:'2026-09-17 21:01:00',remark:order.remark,items:order.items};
  const code = kind === 'print'
    ? 'import json,sys; from app.core.subcontract_documents import subcontract_print_html; sys.stdout.buffer.write(subcontract_print_html(json.loads(sys.argv[1])).encode("utf-8"))'
    : 'import json,sys; from app.core.subcontract_documents import subcontract_xlsx; sys.stdout.buffer.write(subcontract_xlsx(json.loads(sys.argv[1])))';
  return execFileSync((process.env.PYTHON || 'python'),['-c',code,JSON.stringify(doc)],{cwd:path.resolve(__dirname,'..'),env:{...process.env,PYTHONUTF8:'1'}});
}

async function main() {
  const root = path.resolve(__dirname, '../web-v2');
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(root, '.' + (name === '/' ? '/index.html' : name));
    if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    try {
      res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
      res.end(fs.readFileSync(file));
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1262, height: 766 } });
    const errors = [], submitted = [], orders = [], auditRequests = [], rejects = [], resubmits = [];
    let currentRole = 'admin';
    let failOutput = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => sessionStorage.setItem('zhicang-v2-token', 'test-token'));
    await browser.contexts()[0].addInitScript(() => { window.print = () => { window.__printCalls = (window.__printCalls || 0) + 1; }; });
    await page.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url());
      let body = [], status = 200;
      if (/^\/api\/subcontract\/orders\/\d+\/(export|print)$/.test(url.pathname)) {
        assert.equal(request.headers().authorization,'Bearer test-token');
        if (failOutput) { failOutput=false; return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'导出失败，请重试'})}); }
        const order=orders.find(order => order.id===Number(url.pathname.split('/')[4]));
        const printing=url.pathname.endsWith('/print');
        return route.fulfill({status:200,contentType:printing?'text/html; charset=utf-8':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          headers:printing?{}:{'Content-Disposition':"attachment; filename*=UTF-8''"+encodeURIComponent((order.direction==='inbound'?'委外入库单':'委外出库单')+'_'+order.order_no+'.xlsx')},body:documentOutput(order,printing?'print':'export')});
      }
      if (url.pathname === '/api/auth/me') body = { id: 1, username: 'admin', role: currentRole };
      else if (url.pathname === '/api/master/suppliers') body = [{ id: 1, name: '测试加工商' }];
      else if (url.pathname === '/api/master/products') body = [{ id: 1, sku: 'SKU-1', name: '测试商品', unit: '件' }];
      else if (url.pathname === '/api/master/warehouses') body = [{ id: 1, name: '测试仓库' }];
      else if (url.pathname === '/api/subcontract/ocr/upload') body = {
        action: 'recognized', ocr_record_id: 7,
        recognized: { overall_confidence: 0.99, items: [{ product_id: 1, qty: 12 }] },
      };
      else if (/^\/api\/subcontract\/orders\/\d+\/audit$/.test(url.pathname)) {
        const order = orders.find(order => order.id === Number(url.pathname.split('/')[4]));
        auditRequests.push(order.id);
        order.status = 'audited'; order.audited_by = 1; order.audited_by_name = 'admin'; order.audited_at = new Date().toISOString();
        order.revision++;
        body = order;
      } else if (/^\/api\/subcontract\/orders\/\d+\/reject$/.test(url.pathname)) {
        const order = orders.find(order => order.id === Number(url.pathname.split('/')[4]));
        const payload = request.postDataJSON(); rejects.push(payload);
        order.status = 'rejected'; order.rejection_reason = payload.reason;
        order.review_history.push({action:'reject',username:'admin',reason:payload.reason,at:new Date().toISOString()});
        order.revision++; body = order;
      } else if (/^\/api\/subcontract\/orders\/\d+\/resubmit$/.test(url.pathname)) {
        const order = orders.find(order => order.id === Number(url.pathname.split('/')[4]));
        const payload = request.postDataJSON(); resubmits.push(payload);
        Object.assign(order,payload); order.status = 'pending_audit'; order.revision++;
        order.items = payload.items.map(item => ({...item,product_name:'测试商品',sku:'SKU-1',unit:'件'}));
        order.review_history.push({action:'resubmit',username:'admin',at:new Date().toISOString()}); body = order;
      } else if (/^\/api\/subcontract\/orders\/\d+$/.test(url.pathname)) {
        body = orders.find(order => order.id === Number(url.pathname.split('/')[4]));
      } else if (request.method() === 'POST') {
        const payload = request.postDataJSON();
        submitted.push({ path: url.pathname, payload });
        const order = { ...payload, items: payload.items.map(item => ({ ...item, product_name: '测试商品', sku: 'SKU-1', unit: '件' })), id: submitted.length, order_no: 'TEST-' + submitted.length, status: 'pending_audit', created_at: new Date().toISOString() };
        Object.assign(order,{created_by:1,revision:1,review_history:[]}); orders.push(order);
        body = url.pathname.endsWith('/commit') ? { order, reused: false } : order;
        status = 201;
      } else if (url.pathname === '/api/subcontract/orders') body = orders.filter(order => order.direction === url.searchParams.get('direction'));
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(process.env.SUBCONTRACT_UI_URL || 'http://127.0.0.1:' + server.address().port, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.getElementById('accountName').textContent.includes('admin'));
    for (const direction of ['inbound', 'outbound']) {
      await page.evaluate(direction => document.querySelector('[data-action="subcontract-' + direction + '"]').click(), direction);
      await page.waitForFunction(() => document.getElementById('subcontractDialog').open);
      assert.equal(await page.locator('#subcontractModeManual').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('#subcontractImageMode').isVisible(), false);
      assert.equal(await page.locator('#subcontractSubmit').isDisabled(), true);
      await page.selectOption('#subcontractSupplier', '1');
      await page.selectOption('#subcontractWarehouse', '1');
      await page.selectOption('#subcontractItemRows .product', '1');
      await page.fill('#subcontractItemRows .qty', '0');
      assert.equal(await page.locator('#subcontractSubmit').isDisabled(), true);
      await page.fill('#subcontractItemRows .qty', '10');
      assert.equal(await page.locator('#subcontractSubmit').isEnabled(), true);
      await page.click('#subcontractModeImage');
      assert.equal(await page.locator('#subcontractImageMode').isVisible(), true);
      assert.equal(await page.locator('#subcontractSubmit').isDisabled(), true);
      await page.click('#subcontractModeManual');
      assert.equal(await page.locator('#subcontractSubmit').isEnabled(), true);
      await page.click('#subcontractSubmit');
      await page.waitForFunction(() => document.getElementById('subcontractItemRows').querySelector('.qty').value === '');
      const latest = submitted.at(-1);
      assert.equal(latest.path, '/api/subcontract/orders');
      assert.equal(latest.payload.source, 'manual');
      assert.equal(latest.payload.direction, direction);
      assert.equal(latest.payload.items[0].qty, 10);
      assert.equal(await page.locator('#subcontractSubmit').isDisabled(), true);
      await page.locator('[data-action="subcontract-detail"][data-id="' + orders.at(-1).id + '"]').click();
      await page.waitForFunction(() => document.querySelector('[data-action="subcontract-audit"]'));
      assert.match(await page.locator('#subcontractDetailContent').innerText(), /SKU-1/);
      const beforeRejects = rejects.length;
      await page.click('[data-action="subcontract-reject"]');
      assert.match(await page.locator('#subcontractDetailError').innerText(), /填写驳回原因/);
      await page.fill('#subcontractRejectReason', '数量有误，需要修改');
      page.once('dialog', dialog => dialog.dismiss()); await page.click('[data-action="subcontract-reject"]');
      assert.equal(rejects.length,beforeRejects);
      page.once('dialog', dialog => dialog.accept()); await page.click('[data-action="subcontract-reject"]');
      await page.waitForFunction(() => document.querySelector('[data-action="subcontract-edit"]'));
      assert.equal(rejects.length,beforeRejects+1);
      assert.match(await page.locator('#subcontractDetailContent').innerText(),/数量有误，需要修改/);
      assert.equal(await page.locator('[data-action="subcontract-audit"]').count(),0);
      if (process.env.SUBCONTRACT_SCREENSHOTS && direction === 'inbound') await page.screenshot({path:'test-artifacts/subcontract-rejected.png'});
      const orderId = orders.at(-1).id, originalNumber = orders.at(-1).order_no;
      await page.click('[data-action="subcontract-edit"]');
      assert.equal(await page.locator('#subcontractModes').isVisible(),false);
      assert.equal(await page.locator('#subcontractItemRows .qty').inputValue(),'10');
      await page.fill('#subcontractItemRows .qty','0');
      assert.equal(await page.locator('#subcontractSubmit').isDisabled(),true);
      await page.fill('#subcontractItemRows .qty','20');
      if (process.env.SUBCONTRACT_SCREENSHOTS && direction === 'inbound') await page.screenshot({path:'test-artifacts/subcontract-resubmit.png'});
      page.once('dialog', dialog => dialog.dismiss()); await page.click('#subcontractCancelEdit');
      assert.equal(await page.locator('#subcontractSubmit').innerText(),'重新提交审核');
      await page.click('#subcontractSubmit');
      await page.waitForFunction(() => document.getElementById('subcontractItemRows').querySelector('.qty').value === '');
      assert.equal(resubmits.at(-1).items[0].qty,20);
      assert.equal(orders.at(-1).order_no,originalNumber);
      assert.equal(submitted.length,orderId);
      await page.locator('[data-action="subcontract-detail"][data-id="'+orderId+'"]').click();
      await page.waitForFunction(() => document.querySelector('[data-action="subcontract-audit"]'));
      assert.match(await page.locator('#subcontractDetailContent').innerText(),/处理记录/);
      const previousAudits = auditRequests.length;
      page.once('dialog', dialog => dialog.dismiss());
      await page.click('[data-action="subcontract-audit"]');
      assert.equal(auditRequests.length, previousAudits);
      page.once('dialog', dialog => dialog.accept());
      await page.click('[data-action="subcontract-audit"]');
      await page.waitForFunction(() => document.getElementById('subcontractDetailContent').textContent.includes('已审核'));
      assert.equal(auditRequests.length, previousAudits + 1);
      assert.equal(await page.locator('[data-action="subcontract-audit"]').count(), 0);
      assert.equal(await page.locator('[data-action="subcontract-export"]').count(),1);
      failOutput=true;
      await page.click('[data-action="subcontract-export"]');
      await page.waitForFunction(() => document.getElementById('subcontractDetailError').textContent.includes('导出失败'));
      assert.equal(await page.locator('[data-action="subcontract-export"]').isEnabled(),true);
      const downloadEvent=page.waitForEvent('download'); await page.click('[data-action="subcontract-export"]');
      const download=await downloadEvent;
      assert.match(download.suggestedFilename(),new RegExp((direction==='inbound'?'委外入库单':'委外出库单')+'_'+originalNumber+'\\.xlsx'));
      assert.equal(orders.at(-1).status,'audited');
      await page.evaluate(() => { window.__realOpen=window.open; window.open=()=>null; });
      await page.click('[data-action="subcontract-print"]');
      assert.match(await page.locator('#subcontractDetailError').innerText(),/允许弹出窗口/);
      await page.evaluate(() => { window.open=window.__realOpen; delete window.__realOpen; });
      const popupEvent=page.waitForEvent('popup'); await page.click('[data-action="subcontract-print"]');
      const popup=await popupEvent;
      await popup.waitForSelector('#printDocument');
      await popup.waitForFunction(() => window.__printCalls >= 1);
      assert.match(await popup.locator('h1').innerText(),new RegExp(direction==='inbound'?'委外入库单':'委外出库单'));
      assert.equal(await popup.locator('tbody tr').count(),1);
      assert.match(await popup.locator('tbody').innerText(),/20/);
      await popup.click('#printDocument'); assert.equal(await popup.evaluate(() => window.__printCalls),2);
      if (process.env.SUBCONTRACT_SCREENSHOTS) {
        await popup.screenshot({path:'test-artifacts/subcontract-print-'+direction+'.png'});
        await popup.pdf({path:'test-artifacts/subcontract-print-'+direction+'.pdf',preferCSSPageSize:true,printBackground:true});
      }
      await popup.close();
      await page.locator('#subcontractDetailDialog [data-action="close"]').click();
      // 保留图片识别模式的提交路径，确认不误用手动接口。
      await page.click('#subcontractModeImage');
      await page.setInputFiles('#subcontractFileInput', {
        name: 'test.png', mimeType: 'image/png',
        buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBb8AAAAASUVORK5CYII=', 'base64'),
      });
      await page.click('#subcontractRecognizeBtn');
      await page.waitForFunction(() => document.getElementById('subcontractImageStatus').textContent.includes('识别完成'));
      await page.selectOption('#subcontractSupplier', '1');
      await page.selectOption('#subcontractWarehouse', '1');
      assert.equal(await page.locator('#subcontractSubmit').isEnabled(), true);
      await page.click('#subcontractSubmit');
      await page.waitForFunction(() => document.getElementById('subcontractItemRows').querySelector('.qty').value === '');
      assert.equal(submitted.at(-1).path, '/api/subcontract/ocr/7/commit');
      assert.equal(submitted.at(-1).payload.source, 'ocr');
      await page.locator('#subcontractDialog [data-action="close"]').click();
    }
    for (const role of ['auditor', 'viewer']) {
      currentRole = role;
      await page.reload({ waitUntil: 'networkidle' });
      await page.waitForFunction(role => document.getElementById('accountName').textContent.includes(role === 'auditor' ? '审核员' : '只读'), role);
      await page.evaluate(() => document.querySelector('[data-action="subcontract-inbound"]').click());
      await page.waitForFunction(() => document.getElementById('subcontractDialog').open);
      assert.equal(await page.locator('#subcontractForm').isVisible(), false);
      await page.locator('[data-action="subcontract-detail"][data-id="2"]').click();
      await page.waitForFunction(() => document.getElementById('subcontractDetailContent').textContent.includes('SKU-1'));
      assert.equal(await page.locator('[data-action="subcontract-audit"]').count(), role === 'auditor' ? 1 : 0);
      assert.equal(await page.locator('[data-action="subcontract-reject"]').count(),role === 'auditor' ? 1 : 0);
      await page.locator('#subcontractDetailDialog [data-action="close"]').click();
      await page.locator('#subcontractDialog [data-action="close"]').click();
    }
    orders.find(order => order.id === 2).status = 'rejected';
    for (const role of ['auditor','viewer','operator']) {
      currentRole = role;
      orders.find(order => order.id === 2).created_by = 99;
      await page.reload({waitUntil:'networkidle'});
      await page.waitForFunction(() => document.getElementById('accountName').textContent !== '未登录');
      await page.evaluate(() => document.querySelector('[data-action="subcontract-inbound"]').click());
      await page.locator('[data-action="subcontract-detail"][data-id="2"]').click();
      await page.waitForFunction(() => document.getElementById('subcontractDetailContent').textContent.includes('已驳回'));
      assert.equal(await page.locator('[data-action="subcontract-edit"]').count(),0);
      assert.equal(await page.locator('[data-action="subcontract-audit"]').count(),0);
      await page.locator('#subcontractDetailDialog [data-action="close"]').click();
      await page.locator('#subcontractDialog [data-action="close"]').click();
    }
    assert.deepEqual(errors, []);
    console.log('PASS: 委外双方向双模式、驳回重提、审核权限、Excel 下载及失败重试、A4 打印（模拟操作，正式单据 0）。');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

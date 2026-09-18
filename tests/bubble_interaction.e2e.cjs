// 仅检查首页视觉和互动，不调用业务写入接口。
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');

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
    const page = await browser.newPage({ viewport: { width: 1262, height: 770 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.BUBBLE_UI_URL || 'http://127.0.0.1:' + server.address().port, { waitUntil: 'networkidle' });
    const tile = page.locator('.hero-tile'), bubble = page.locator('.hero-bubble');
    const activeClicks = () => bubble.evaluate(e => e.getAnimations().filter(a => a.constructor.name === 'Animation').length);
    await page.waitForTimeout(600);
    let box = await tile.boundingBox();
    await page.mouse.move(box.x + box.width * .8, box.y + box.height * .4);
    assert.ok(parseFloat(await page.locator('.bubble-interaction').evaluate(e => e.style.getPropertyValue('--bubble-x'))) > 0);
    await page.mouse.move(10, 100);
    assert.equal(await page.locator('.bubble-interaction').evaluate(e => e.style.getPropertyValue('--bubble-x')), '0px');
    await page.evaluate(() => document.getElementById('startWorkBtn').click());
    box = await tile.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    assert.equal(await activeClicks(), 1);
    assert.equal(await page.locator('#stage').evaluate(e => e.classList.contains('working')), false);
    // 采样点击动画的最大压扁阶段，排除机器执行速度影响。
    const scales = await bubble.evaluate(e => {
      const animation = e.getAnimations().find(a => a.constructor.name === 'Animation');
      animation.pause(); animation.effect.updateTiming({ easing: 'linear' }); animation.currentTime = 170;
      const matrix = new DOMMatrix(getComputedStyle(e).transform);
      return { x: Math.hypot(matrix.a, matrix.b), y: Math.hypot(matrix.c, matrix.d) };
    });
    assert.ok(scales.x > 1.5 && scales.y < .6, JSON.stringify(scales));
    if (process.env.BUBBLE_SCREENSHOT) await page.screenshot({ path: process.env.BUBBLE_SCREENSHOT });
    await bubble.evaluate(e => e.getAnimations().find(a => a.constructor.name === 'Animation').play());
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    assert.equal(await activeClicks(), 1);
    await page.waitForTimeout(950);
    assert.equal(await activeClicks(), 0);
    await tile.focus(); await page.keyboard.press('Enter');
    assert.equal(await activeClicks(), 1);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForFunction(() => document.querySelector('.hero-bubble').getAnimations().length === 0);
    await page.keyboard.press('Enter');
    assert.equal(await activeClicks(), 0);
    assert.equal(await page.locator('.bubble-interaction').evaluate(e => getComputedStyle(e).transform), 'none');
    assert.deepEqual(errors, []);
    console.log('PASS: 水泡鼠标跟随、离开复位、大幅点击变形、连续点击、收起、键盘和减少动画设置。');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

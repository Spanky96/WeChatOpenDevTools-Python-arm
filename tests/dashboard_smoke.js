const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const APP = 'wx0000000000000001', OTHER = 'wx0000000000000002';
const now = Date.now() / 1000;
const events = [];
for (let index = 0; index < 3; index++) {
  events.push({ t: now, agent_t: now * 1000, appid: APP, type: 'request', data: {
    id: 'request-' + index, url: `https://api.example.test/v1/${['orders', 'inventory', 'profile'][index]}?page=1`,
    method: index === 1 ? 'POST' : 'GET', api: 'request', header: { 'Content-Type': 'application/json' }, data: index === 1 ? { count: 3 } : undefined } });
  events.push({ t: now + .1, agent_t: now * 1000 + 125, appid: APP, type: 'response', data: {
    id: 'request-' + index, url: events.at(-1).data.url, status: index === 2 ? 500 : 200,
    header: { 'Content-Type': 'application/json' }, data: index === 2 ? { error: 'Example failure' } : { ok: true, items: [{ name: '示例商品', count: 3 }] },
    capture: { body: { truncated: index === 1, captured_chars: 256, original_chars: 600, limit: 256 } } } });
}
for (let index = 0; index < 241; index++) events.push({ t: now, appid: APP, type: 'console', data: { level: 'log', args: ['Event ' + index] } });
events.push({ t: now, appid: APP, type: 'console', data: { level: 'error', args: ['Example error', '<script>window.injection=true</script>'] } });
let settings = { request_body: 32768, response_body: 131072, headers: 16384 };

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light', permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      let result = {};
      if (url.pathname === '/api/status') result = { bridge: 31267, bridge_state: 'up', sniper: true, gateway: true, sess: true,
        targets: [{ appid: APP, url: `https://servicewechat.com/${APP}/50/page-frame.html`, targetId: 'ABC123' },
          { appid: OTHER, url: `https://servicewechat.com/${OTHER}/0/page-frame.html`, targetId: 'DEF123' }],
        mps: { [APP]: { phase: 'online', agent_online: true, agent_version: 8, heartbeat_at: now, last_event_at: now },
          [OTHER]: { phase: 'retrying', retry_in: 4, attempts: 2, reason: '等待 service 上下文' } }, capture_settings: settings };
      if (url.pathname === '/api/events') { const offset = Number(url.searchParams.get('cursor') || 0), end = Math.min(offset + 200, events.length); result = { events: events.slice(offset, end), cursor: String(end), has_more: end < events.length, reset: false }; }
      if (url.pathname === '/api/settings') { if (route.request().method() === 'POST') settings = route.request().postDataJSON().settings; result = { ok: true, settings }; }
      if (url.pathname === '/api/storage') result = { data: { sz: 4, limit: 10240, kv: { example: JSON.stringify({ enabled: true }), locale: 'zh-CN' } } };
      if (url.pathname === '/api/files') result = { files: ['app-service.js'] };
      if (url.pathname === '/api/source') result = { lines: ['const example = 1;', 'function run() { return example; }', 'run();'] };
      if (['/api/retry', '/api/open_devtools', '/api/breakpoint', '/api/control', '/api/evaluate'].includes(url.pathname)) result = { ok: true, value: '2' };
      await route.fulfill({ json: result });
    });
    await page.route('http://127.0.0.1:8778/**', route => route.fulfill({ json: { ok: true, value: '2' }, headers: { 'Access-Control-Allow-Origin': '*' } }));
    await page.goto(process.env.DASHBOARD_URL || 'http://127.0.0.1:8777/');
    await page.locator(`[data-app="${APP}"]`).click();
    await page.waitForFunction(() => document.getElementById('net-rows').children.length === 3);
    assert.equal(await page.locator('#net-rows tr').count(), 3, 'requests must be correlated');
    assert.equal(await page.locator('#con-badge').textContent(), '242', 'all pages must be drained');
    assert.equal(await page.evaluate(() => window.injection), undefined);
    await page.locator('#net-state').selectOption('error');
    assert.equal(await page.locator('#net-rows tr').count(), 1);
    await page.locator('#net-state').selectOption('truncated');
    await page.locator('#net-rows tr').first().click();
    await page.locator('[data-detail-tab="response"]').click();
    assert.match(await page.locator('.capture-warning').textContent(), /256 \/ 600/);
    await page.locator('#net-state').selectOption('');
    await page.locator('[data-detail-action="url"]').click();
    assert.match(await page.evaluate(() => navigator.clipboard.readText()), /inventory/);
    const [exported] = await Promise.all([page.waitForEvent('download'), page.locator('#export-network').click()]);
    assert.match(exported.suggestedFilename(), /network/);
    await page.screenshot({ path: '/tmp/wechat_rea/dashboard-desktop.png', fullPage: true });
    await page.locator('#settings-open').click();
    await page.locator('[name="response_body"]').fill('65536');
    await page.locator('#settings-save').click();
    assert.equal(settings.response_body, 65536);
    await page.getByRole('tab', { name: /^Console/ }).click();
    await page.locator('#con-level').selectOption('error');
    assert.equal(await page.locator('.cline').count(), 1);
    await page.locator('#pause-console').click();
    events.push({ t: now + 1, appid: APP, type: 'console', data: { level: 'error', args: ['arrived while paused'] } });
    await page.waitForTimeout(1800);
    assert.equal(await page.locator('.cline').count(), 1);
    await page.locator('#pause-console').click();
    assert.equal(await page.locator('.cline').count(), 2);
    await page.getByRole('tab', { name: 'Storage' }).click();
    await page.waitForFunction(() => document.getElementById('st-rows').children.length === 2);
    await page.locator('#storage-filter').fill('locale');
    assert.equal(await page.locator('#st-rows tr').count(), 1);
    await page.getByRole('tab', { name: '源码', exact: true }).click();
    await page.waitForSelector('.srcline');
    await page.locator('#src-format').click();
    assert.match(await page.locator('#src-info').textContent(), /不支持设置断点/);
    await page.locator('#src-raw').click();
    await page.locator('[data-breakpoint="1"]').click();
    await page.waitForSelector('.srcline.bp');
    await page.getByRole('tab', { name: '连接诊断' }).click();
    assert.match(await page.locator('#diagnostic-values').textContent(), /v8/);
    await page.locator(`[data-app="${OTHER}"]`).click();
    assert.match(await page.locator('#session-phase').textContent(), /等待重试/);
    await page.locator(`[data-app="${APP}"]`).click();
    await page.getByRole('tab', { name: /^Network/ }).click();
    await page.locator('#net-rows tr').first().click();
    for (const [width, height] of [[1920, 1080], [900, 900], [760, 800], [390, 844], [320, 700]]) {
      await page.setViewportSize({ width, height });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, `horizontal overflow at ${width}`);
      assert.equal(await page.locator('#net-detail').isVisible(), true);
      if (width === 390) {
        await page.waitForTimeout(250);
        await page.screenshot({ path: '/tmp/wechat_rea/dashboard-mobile.png', fullPage: true, animations: 'disabled' });
      }
    }
    await page.locator('[data-detail-action="close"]').last().click();
    assert.equal(await page.locator('#net-detail').isVisible(), false);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForTimeout(250);
    await page.screenshot({ path: '/tmp/wechat_rea/dashboard-dark.png', fullPage: true, animations: 'disabled' });
    assert.deepEqual(errors, []);
    console.log('Dashboard browser checks passed: pagination, filters, detail, clipboard, export, settings, Console pause, Storage, source, diagnostics, desktop/mobile/dark layouts.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });

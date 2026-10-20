const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require('playwright');

(async () => {
  const fixture = spawn('python3', ['-u', require('node:path').join(__dirname, 'storage_devtools_fixture.py')]);
  let browser;
  try {
    const port = await new Promise((resolve, reject) => {
      fixture.stdout.once('data', chunk => resolve(Number(chunk.toString().split('\n')[0])));
      fixture.once('error', reject);
      fixture.once('exit', code => reject(new Error('fixture exited: ' + code)));
    });
    browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(`devtools://devtools/bundled/inspector.html?ws=127.0.0.1:${port}/devtools/page/ABC123&panel=resources`);
    const origin = page.getByRole('treeitem', { name: /https:\/\/wx0000000000000001.wx-storage.invalid/ }).first();
    await page.getByRole('treeitem', { name: 'Local storage', exact: true }).dblclick();
    try { await origin.waitFor({ timeout: 10000 }); }
    catch (error) {
      await page.screenshot({ path: '/tmp/wechat_rea/storage-devtools-error.png', fullPage: true });
      console.error(await page.getByRole('treeitem').allTextContents());
      throw error;
    }
    await origin.click();
    await page.waitForFunction(() => {
      const text = (root) => [...root.querySelectorAll('*')].map(element =>
        (element.shadowRoot ? text(element.shadowRoot) : '') + (element.childElementCount ? '' : element.textContent)).join(' ');
      const content = text(document);
      return content.includes('example') && content.includes('zh-CN');
    });
    assert.ok(await page.getByText('example', { exact: true }).count() >= 1);
    await page.screenshot({ path: '/tmp/wechat_rea/storage-devtools.png', fullPage: true });
    console.log('Chrome DevTools Application > Local storage displays typed wx Storage through the real CDP adapter.');
  } finally {
    if (browser) await browser.close();
    if (fixture.exitCode === null && fixture.signalCode === null) {
      const exited = once(fixture, 'exit');
      fixture.kill('SIGKILL');
      await exited;
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

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
    let sourceChars = 0;
    page.on('websocket', socket => socket.on('framereceived', frame => {
      try {
        const message = JSON.parse(frame.payload);
        if (typeof message.result?.scriptSource === 'string') sourceChars = message.result.scriptSource.length;
      } catch (_) {}
    }));
    const endpoint = `127.0.0.1:${port}/devtools/page/ABC123?appid=wx0000000000000001`;
    await page.goto('devtools://devtools/bundled/devtools_app.html?ws=' + encodeURIComponent(endpoint) + '&panel=network');
    await page.waitForFunction(async () => {
      const Logs = await import('./models/logs/logs.js');
      return Logs.NetworkLog.NetworkLog.instance().requests()
        .some(request => request.url().endsWith('/fixture'));
    });
    await page.locator('button.devtools-link').filter({ hasText: 'app-service.js:1' }).first().click();
    await page.getByRole('tab', { name: /app-service.js/ }).first().waitFor();
    await page.locator('devtools-text-editor.pretty-printed').waitFor();
    await page.waitForFunction(() => {
      const text = root => [...root.querySelectorAll('*')].map(element =>
        (element.shadowRoot ? text(element.shadowRoot) : '') +
        (element.childElementCount ? '' : element.textContent)).join(' ');
      const content = text(document);
      return content.includes('loadData') && content.includes('return') && !content.includes('Formatting…');
    }, null, { timeout: 30000 });
    assert.equal(await page.getByText('This site can’t be reached', { exact: true }).count(), 0);
    assert.ok(sourceChars > 1_000_000, 'frontend must receive the complete script source');
    await page.screenshot({ path: '/tmp/wechat_rea/source-devtools.png', fullPage: true });
    console.log('Chrome DevTools encoded URL, Network initiator source jump and 1.16 MB source formatting passed.');
  } catch (error) {
    if (browser) {
      const page = browser.contexts()[0].pages()[0];
      await page.screenshot({ path: '/tmp/wechat_rea/source-devtools-error.png', fullPage: true });
      console.error('tabs', await page.getByRole('tab').allTextContents());
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    if (fixture.exitCode === null && fixture.signalCode === null) {
      const exited = once(fixture, 'exit');
      fixture.kill('SIGKILL');
      await exited;
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

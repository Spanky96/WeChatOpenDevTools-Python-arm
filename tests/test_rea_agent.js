const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../scripts/rea_agent.js'), 'utf8');

function fixture() {
  const intervals = new Map(), timeouts = new Map(), sockets = [];
  let nextTimer = 0;
  const wx = {
    connectSocket() {
      const socket = { handlers: {}, sent: [], callbacks: [], rejectSend: false,
        onOpen(fn) { this.handlers.open = fn; },
        onClose(fn) { this.handlers.close = fn; },
        onError(fn) { this.handlers.error = fn; },
        onMessage() {},
        send(options) {
          if (this.rejectSend) throw new Error('temporarily unavailable');
          this.sent.push(JSON.parse(options.data));
          this.callbacks.push(options);
        },
        close() { this.handlers.close(); },
      };
      sockets.push(socket);
      return socket;
    },
    onError() {},
  };
  Object.defineProperty(wx, 'request', {
    configurable: true, writable: false,
    value(options) {
      options.success({ statusCode: 200, header: {}, data: { ok: true } });
      return 'request-task';
    },
  });
  const context = vm.createContext({ wx, console: { log() {}, info() {}, warn() {}, error() {} },
    __wxConfig: { accountInfo: { appId: 'wx0000000000000001' } },
    Date: { now: () => 123456 },
    setInterval(fn, delay) { intervals.set(++nextTimer, { fn, delay }); return nextTimer; },
    clearInterval(id) { intervals.delete(id); },
    setTimeout(fn, delay) { timeouts.set(++nextTimer, { fn, delay }); return nextTimer; },
    clearTimeout(id) { timeouts.delete(id); },
  });
  vm.runInContext(source, context);
  return { context, intervals, timeouts, sockets,
    flush() { for (const timer of intervals.values()) if (timer.delay === 250) timer.fn(); },
  };
}

{
  const f = fixture(), socket = f.sockets[0];
  socket.handlers.open();
  assert.equal(f.context.wx.request({ url: 'https://test.invalid' }), 'request-task');
  assert.equal(socket.sent.length, 1);
  socket.callbacks[0].success();
  f.flush();
  assert.equal(socket.sent.length, 2);
  socket.callbacks[1].success();
  f.flush();
  assert.equal(socket.sent.length, 2, 'flush must not resend live events');
  assert.equal(f.context.__REA__.events.length, 0);
  const events = socket.sent.flatMap(batch => batch.events || []);
  assert.deepEqual(events.map(e => e.type), ['request', 'response']);
  assert.equal(events[0].data.id, events[1].data.id);
  f.context.wx.request({ url: 'https://test.invalid/2' });
  assert.notEqual(socket.sent[2].events[0].data.id, events[0].data.id,
    'requests in the same millisecond need distinct ids');
  vm.runInContext(source, f.context);
  assert.equal(f.sockets.length, 1, 'reinjection must be idempotent');
}

{
  const f = fixture(), socket = f.sockets[0];
  socket.handlers.open();
  socket.rejectSend = true;
  f.context.wx.request({ url: 'https://test.invalid' });
  assert.equal(f.context.__REA__.events.length, 2, 'send failure must retain events');
  socket.rejectSend = false;
  f.flush();
  assert.equal(socket.sent[0].events.length, 2);
  socket.callbacks[0].success();
  assert.equal(f.context.__REA__.events.length, 0);
}

{
  const f = fixture(), socket = f.sockets[0];
  socket.handlers.error();
  socket.handlers.close();
  assert.equal(f.timeouts.size, 1, 'onError/onClose must schedule one reconnect');
  const [id, timer] = [...f.timeouts][0];
  f.timeouts.delete(id);
  timer.fn();
  assert.equal(f.sockets.length, 2);
  f.context.__REA__.dispose();
  assert.equal(f.timeouts.size, 0);
  assert.equal(f.intervals.size, 0);
}

console.log('Agent regressions passed: delivery, retry, unique ids, reinjection.');

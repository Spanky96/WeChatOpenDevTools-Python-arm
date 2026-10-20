// __REA__ agent v9 - injected into the mini-program service context.
//
// Captures into globalThis.__REA__.events (polled by service_session.py):
//   request/response/fail  — wx.request family with full detail
//   console                — log/warn/error/info incl. Error stacks
//   route                  — page route changes
//   storage_change         — wx.setStorageSync/remove/clear
//   error                  — wx.onError
// Commands (call via Runtime.evaluate):
//   __REA__.dumpStorage()  -> {keys:[], kv:{}}
//   __REA__.drain(n)       -> events array (and clears)
(function () {
  var previous = globalThis.__REA__;
  if (typeof globalThis.__REA__ !== 'undefined' &&
      globalThis.__REA__.version >= 9) return;
  if (previous && previous.dispose) previous.dispose();

  var events = [];
  var MAX = 3000;
  var boot = Date.now(), sequence = 0, requestSequence = 0;
  var legacyQueues = previous ? (previous.queues || [previous.events]) : [];
  var timers = [], stopped = false, queuedChars = 0;
  var eventSizes = [], MAX_CHARS = 4 * 1024 * 1024;
  var legacyHooks = [];
  var limits = { request_body: 32768, response_body: 131072, headers: 16384 };
  function configure(config) {
    Object.keys(limits).forEach(function (key) {
      var value = config && config[key];
      if (typeof value === 'number' && value >= 256 && value <= 1048576)
        limits[key] = Math.floor(value);
    });
  }
  configure(globalThis.__REA_CAPTURE__);
  function capture(value, limit) {
    var text, snapshot;
    try {
      text = typeof value === 'string' ? value : JSON.stringify(value);
      snapshot = typeof value === 'string' || text === undefined ? value : JSON.parse(text);
      if (text === undefined) snapshot = undefined;
    } catch (e) {
      try { text = String(value); } catch (_) { text = '[unserializable]'; }
      snapshot = text;
    }
    if (typeof text !== 'string') text = '';
    var truncated = text.length > limit;
    return { value: truncated ? text.slice(0, limit) : snapshot,
      meta: { truncated: truncated, original_chars: text.length,
              captured_chars: Math.min(text.length, limit), limit: limit } };
  }
  function report(type, data) {
    var e = { t: Date.now(), seq: boot + '-' + (++sequence),
              type: type, data: data };
    try { if (globalThis.__REA__) globalThis.__REA__.push([e]); } catch (_) {}
  }

  function shallow(x, n) {
    try {
      if (x instanceof Error) {
        return { __err: true, name: x.name, message: x.message,
                 stack: String(x.stack || '').slice(0, 1500) };
      }
      var s = typeof x === 'object' && x !== null
        ? JSON.stringify(x) : String(x);
      return s.length > n ? s.slice(0, n) + '…' : s;
    } catch (e) { return '[unserializable]'; }
  }

  // ---- wx.request family ----
  // wx.request is writable:false (but configurable:true) — plain
  // assignment silently fails; must use Object.defineProperty.
  function wrapRequest(name) {
    try {
      if (typeof wx === 'undefined' || !wx[name]) return;
      var orig = wx[name];
      if (orig.__reaWrapped && !orig.__reaOriginal) {
        legacyHooks.push(name);
        return;
      }
      orig = orig.__reaOriginal || orig;
      function wrapped(opts) {
        opts = opts || {};
        var id = boot + '-' + (++requestSequence);
        var current = globalThis.__REA__;
        var body = current.capture(opts.data, current.limits.request_body);
        var headers = current.capture(opts.header, current.limits.headers);
        report('request', {
          id: id, api: name, url: opts.url,
          method: opts.method || (name === 'uploadFile' ? 'POST' : 'GET'),
          data: body.value, header: headers.value,
          capture: { body: body.meta, headers: headers.meta },
        });
        var w = Object.assign({}, opts);
        w.success = function (res) {
          res = res || {};
          var current = globalThis.__REA__;
          var body = current.capture(res.data, current.limits.response_body);
          var headers = current.capture(res.header, current.limits.headers);
          report('response', {
            id: id, api: name, url: opts.url,
            status: res.statusCode,
            header: headers.value, data: body.value,
            capture: { body: body.meta, headers: headers.meta },
          });
          if (opts.success) opts.success(res);
        };
        w.fail = function (err) {
          report('fail', { id: id, api: name, url: opts.url,
                           err: shallow(err, 800) });
          if (opts.fail) opts.fail(err);
        };
        return orig.call(wx, w);
      }
      wrapped.__reaWrapped = true;
      wrapped.__reaOriginal = orig;
      Object.defineProperty(wx, name,
                            { value: wrapped, writable: true,
                              configurable: true });
    } catch (e) { }
  }
  wrapRequest('request');
  wrapRequest('uploadFile');
  wrapRequest('downloadFile');

  // ---- console with Error stack capture ----
  if (!previous) ['log', 'warn', 'error', 'info'].forEach(function (m) {
    try {
      var o = console[m] ? console[m].bind(console) : function () { };
      console[m] = function () {
        var args = [].slice.call(arguments).map(function (x) {
          return shallow(x, 800);
        });
        // skip our own hub-retry noise (avoid feedback spam)
        var joined = args.join(' ');
        if (joined.indexOf('127.0.0.1:8779') < 0) {
          report('console', { level: m, args: args });
        }
        o.apply(null, arguments);
      };
    } catch (e) { }
  });

  // ---- route tracking ----
  var lastRoute = '';
  function checkRoute() {
    try {
      var ps = typeof getCurrentPages === 'function' && getCurrentPages();
      var r = ps && ps.length ? ps[ps.length - 1].route : '';
      if (r && r !== lastRoute) {
        lastRoute = r;
        report('route', { route: r });
      }
    } catch (e) { }
  }
  timers.push(setInterval(checkRoute, 2000));

  // ---- storage (same writable:false trap — use defineProperty) ----
  ['setStorageSync', 'removeStorageSync', 'clearStorageSync'].forEach(
    function (m) {
      try {
        var orig = wx[m];
        if (!orig || orig.__reaWrapped) return;
        function wrappedS(k, v) {
          report('storage_change', {
            op: m === 'setStorageSync' ? 'set' :
                m === 'removeStorageSync' ? 'remove' : 'clear',
            key: typeof k === 'string' ? k : '',
            value: m === 'setStorageSync' ? shallow(v, 1500) : undefined,
          });
          return orig.apply(wx, arguments);
        }
        wrappedS.__reaWrapped = true;
        Object.defineProperty(wx, m, { value: wrappedS, writable: true,
                                      configurable: true });
      } catch (e) { }
    });

  // ---- errors ----
  if (!previous) try {
    wx.onError(function (msg) { report('error', { msg: shallow(msg, 2000) }); });
  } catch (e) { }

  // ---- direct push hub (wx.connectSocket -> local agent hub) ----
  var sock = null, sockOpen = false, connecting = false;
  var retryTimer = null, backoff = 2000, sending = false;
  var APPID = '';
  try {
    var cfg = typeof __wxConfig !== 'undefined' && __wxConfig;
    APPID = (cfg && (cfg.appId || cfg.accountInfo &&
             cfg.accountInfo.appId)) || 'unknown';
  } catch (e) {}
  function scheduleRetry() {
    if (stopped || retryTimer) return;
    var wait = backoff;
    backoff = Math.min(backoff * 2, 30000);
    retryTimer = setTimeout(function () {
      retryTimer = null;
      connectHub();
    }, wait);
  }
  function connectHub() {
    if (stopped || connecting || sockOpen) return;
    connecting = true;
    try {
      var current = wx.connectSocket({
        url: 'ws://127.0.0.1:8779/agent?appid=' + APPID + '&v=9' });
      sock = current;
      current.onOpen(function () {
        if (stopped || current !== sock) return;
        connecting = false; sockOpen = true; backoff = 2000;
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
        flush();
      });
      current.onClose(function () {
        if (stopped || current !== sock) return;
        connecting = false; sockOpen = false; sending = false; scheduleRetry();
      });
      current.onError(function () {
        if (stopped || current !== sock) return;
        connecting = false; sockOpen = false; sending = false; scheduleRetry();
      });
      current.onMessage(function (message) {
        if (stopped || current !== sock) return;
        try { configure(JSON.parse(message.data).config); } catch (e) {}
      });
    } catch (e) { connecting = false; scheduleRetry(); }
  }
  function reconnect() {
    if (stopped) return;
    var previousSocket = sock;
    sock = null; sockOpen = false; connecting = false; sending = false;
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
    backoff = 2000;
    try { if (previousSocket) previousSocket.close({}); } catch (_) {}
    connectHub();
  }
  function flush() {
    if (stopped || !sockOpen || sending || !events.length) return;
    var count = 0, chars = 0;
    while (count < events.length && count < 500) {
      var size = eventSizes[count] || JSON.stringify(events[count]).length;
      if (count && chars + size > 1024 * 1024) break;
      chars += size;
      count++;
    }
    var batch = events.slice(0, count);
    sending = true;
    try {
      sock.send({ data: JSON.stringify({ events: batch }),
        success: function () {
          [events].concat(legacyQueues).forEach(function (queue) {
            if (!queue) return;
            batch.forEach(function (e) {
              var at = queue.indexOf(e);
              if (at >= 0) {
                queue.splice(at, 1);
                if (queue === events) queuedChars -= eventSizes.splice(at, 1)[0] || 0;
              }
            });
          });
          sending = false;
        },
        fail: function () { sending = false; }
      });
    } catch (e) { sending = false; }
  }
  timers.push(setInterval(flush, 250));
  timers.push(setInterval(function () {
    if (sockOpen) { try { sock.send({ data: '{"hb":1}' }); } catch (e) {} }
  }, 10000));

  globalThis.__REA__ = {
    events: events, queues: [events].concat(legacyQueues), version: 9, boot: boot,
    capture: capture, limits: limits, configure: configure, legacyHooks: legacyHooks,
    reconnect: reconnect,
    push: function (batch) {
      batch.forEach(function (e) {
        if (events.indexOf(e) < 0) {
          var size = JSON.stringify(e).length;
          events.push(e); eventSizes.push(size); queuedChars += size;
        }
      });
      while (events.length > MAX || (queuedChars > MAX_CHARS && events.length > 1)) {
        events.shift(); queuedChars -= eventSizes.shift() || 0;
      }
      flush();
    },
    dispose: function () {
      stopped = true;
      timers.forEach(clearInterval);
      if (retryTimer) clearTimeout(retryTimer);
      try { if (sock) sock.close({}); } catch (e) {}
    },
    dumpStorage: function () {
      try {
        var info = wx.getStorageInfoSync();
        var kv = {};
        (info.keys || []).slice(0, 100).forEach(function (k) {
          kv[k] = shallow(wx.getStorageSync(k), 2000);
        });
        return { keys: info.keys || [], currentSize: info.currentSize,
                 limitSize: info.limitSize, kv: kv };
      } catch (e) { return { err: String(e) }; }
    },
    drain: function (n) {
      var out = events.splice(0, n || events.length);
      eventSizes.splice(0, out.length).forEach(function (size) { queuedChars -= size; });
      return out;
    },
  };
  connectHub();
})();

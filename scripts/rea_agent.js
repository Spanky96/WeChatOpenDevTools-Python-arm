// __REA__ agent v7 - injected into the mini-program service context.
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
      globalThis.__REA__.version >= 7) return;
  if (previous && previous.dispose) previous.dispose();

  var events = [];
  var MAX = 3000;
  var boot = Date.now(), sequence = 0, requestSequence = 0;
  var legacyQueues = previous ? (previous.queues || [previous.events]) : [];
  var timers = [], stopped = false;
  function report(type, data) {
    if (events.length >= MAX) events.splice(0, events.length - MAX);
    var e = { t: Date.now(), seq: boot + '-' + (++sequence),
              type: type, data: data };
    events.push(e);
    if (globalThis.__REA__) globalThis.__REA__.push([e]);
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
      if (orig.__reaWrapped) return;
      function wrapped(opts) {
        opts = opts || {};
        var id = boot + '-' + (++requestSequence);
        report('request', {
          id: id, api: name, url: opts.url,
          method: opts.method || 'GET',
          data: shallow(opts.data, 3000),
          header: shallow(opts.header, 1000),
        });
        var w = Object.assign({}, opts);
        w.success = function (res) {
          res = res || {};
          report('response', {
            id: id, api: name, url: opts.url,
            status: res.statusCode,
            header: shallow(res.header, 1000),
            data: shallow(res.data, 8000),
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
        url: 'ws://127.0.0.1:8779/agent?appid=' + APPID + '&v=7' });
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
      current.onMessage(function () {});
    } catch (e) { connecting = false; scheduleRetry(); }
  }
  function flush() {
    if (stopped || !sockOpen || sending || !events.length) return;
    var batch = events.slice(0, 500);
    sending = true;
    try {
      sock.send({ data: JSON.stringify({ events: batch }),
        success: function () {
          [events].concat(legacyQueues).forEach(function (queue) {
            if (!queue) return;
            batch.forEach(function (e) {
              var at = queue.indexOf(e);
              if (at >= 0) queue.splice(at, 1);
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
    events: events, queues: [events].concat(legacyQueues), version: 7, boot: boot,
    push: function (batch) {
      batch.forEach(function (e) {
        if (events.indexOf(e) < 0) events.push(e);
      });
      if (events.length > MAX) events.splice(0, events.length - MAX);
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
      return out;
    },
  };
  connectHub();
})();

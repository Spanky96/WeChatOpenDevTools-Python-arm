// __REA__ agent v2 — injected into the mini-program service context.
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
  if (typeof globalThis.__REA__ !== 'undefined' &&
      globalThis.__REA__.version >= 3) return;

  var events = [];
  var MAX = 3000;
  function report(type, data) {
    if (events.length >= MAX) events.splice(0, events.length - MAX);
    events.push({ t: Date.now(), type: type, data: data });
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
        var id = Date.now() % 1e9 + '-' + events.length;
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
  ['log', 'warn', 'error', 'info'].forEach(function (m) {
    try {
      var o = console[m] ? console[m].bind(console) : function () { };
      console[m] = function () {
        var args = [].slice.call(arguments).map(function (x) {
          return shallow(x, 800);
        });
        report('console', { level: m, args: args });
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
  setInterval(checkRoute, 2000);

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
  try {
    wx.onError(function (msg) { report('error', { msg: shallow(msg, 2000) }); });
  } catch (e) { }

  globalThis.__REA__ = {
    events: events, version: 3, boot: Date.now(),
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
})();

// __REA__ agent — designed for injection into the mini-program service
// context once we locate the native evaluate primitive.
//
// Captures (stored in globalThis.__REA__.events, polled by the host):
//   - wx.request / wx.uploadFile calls with url/method/data/headers
//   - success/fail responses with status + truncated body
//   - console.log/warn/error/info
//   - uncaught errors
(function () {
  if (typeof globalThis.__REA__ !== 'undefined') return;

  var events = [];
  var MAX = 2000;
  function report(type, data) {
    if (events.length >= MAX) events.shift();
    events.push({ t: Date.now(), type: type, data: data });
  }

  function shallow(x, n) {
    try {
      var s = typeof x === 'object' ? JSON.stringify(x) : String(x);
      return s.length > n ? s.slice(0, n) + '…' : s;
    } catch (e) { return '[unserializable]'; }
  }

  // ---- wx.request family ----
  function wrapRequest(name, makeTask) {
    try {
      if (typeof wx === 'undefined' || !wx[name]) return;
      var orig = wx[name].bind(wx);
      wx[name] = function (opts) {
        opts = opts || {};
        var id = events.length;
        report('request', {
          id: id, api: name, url: opts.url,
          method: opts.method || 'GET',
          data: shallow(opts.data, 2000),
          header: shallow(opts.header, 800),
        });
        var wrapped = Object.assign({}, opts);
        wrapped.success = function (res) {
          res = res || {};
          report('response', {
            id: id, api: name, url: opts.url,
            status: res.statusCode,
            data: shallow(res.data, 4000),
          });
          if (opts.success) opts.success(res);
        };
        wrapped.fail = function (err) {
          report('fail', {
            id: id, api: name, url: opts.url,
            err: shallow(err, 500),
          });
          if (opts.fail) opts.fail(err);
        };
        return orig(wrapped);
      };
    } catch (e) { }
  }
  wrapRequest('request');
  wrapRequest('uploadFile');
  wrapRequest('downloadFile');

  // ---- console ----
  ['log', 'warn', 'error', 'info'].forEach(function (m) {
    try {
      var o = console[m] ? console[m].bind(console) : function () { };
      console[m] = function () {
        var args = [].slice.call(arguments).map(function (x) {
          return shallow(x, 500);
        });
        report('console', { level: m, args: args });
        o.apply(null, arguments);
      };
    } catch (e) { }
  });

  // ---- errors ----
  if (typeof wx !== 'undefined' && wx.onError) {
    wx.onError(function (msg) { report('error', { msg: shallow(msg, 800) }); });
  }

  globalThis.__REA__ = { events: events, version: 1, boot: Date.now() };
})();

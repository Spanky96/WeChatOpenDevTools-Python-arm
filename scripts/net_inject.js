// Injected into mini-program page context via CDP Runtime.evaluate
// Intercepts wx.request / wx.uploadFile / wx.downloadFile and reports
// through console.log with a magic prefix for the gateway to parse.
(function() {
    if (typeof __netInjected !== 'undefined') return;
    window.__netInjected = true;

    function report(type, data) {
        try {
            console.log('__NET__' + JSON.stringify({type: type, data: data, ts: Date.now()}));
        } catch(e) {}
    }

    // find wx object - might be in different contexts
    var wxObj = typeof wx !== 'undefined' ? wx : (typeof __wxService !== 'undefined' ? __wxService : null);
    if (!wxObj) {
        // try getApp / getCurrentPages context
        try {
            var pages = getCurrentPages();
            if (pages && pages[0] && pages[0].__proto__) wxObj = pages[0].__proto__.__proto__;
        } catch(e) {}
    }

    if (wxObj && typeof wxObj.request === 'function') {
        var origRequest = wxObj.request.bind(wxObj);
        wxObj.request = function(opts) {
            var id = 'req_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
            report('request', {
                id: id,
                url: opts.url || '',
                method: opts.method || 'GET',
                headers: opts.header || {},
                data: typeof opts.data === 'string' ? opts.data : JSON.stringify(opts.data || null)
            });
            var origSuccess = opts.success;
            var origFail = opts.fail;
            opts.success = function(res) {
                report('response', {
                    id: id,
                    url: opts.url || '',
                    status: res.statusCode || 200,
                    statusText: res.errMsg || '',
                    headers: res.header || {},
                    body: typeof res.data === 'string' ? res.data.substring(0, 4096) : JSON.stringify(res.data || null).substring(0, 4096)
                });
                if (origSuccess) origSuccess(res);
            };
            opts.fail = function(err) {
                report('response', {
                    id: id,
                    url: opts.url || '',
                    status: 0,
                    statusText: err.errMsg || 'failed',
                    headers: {},
                    body: ''
                });
                if (origFail) origFail(err);
            };
            return origRequest(opts);
        };
        report('ready', {intercepted: 'wx.request'});
    } else {
        report('no_wx', {});
    }
})();

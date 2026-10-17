// Inject into mini-program page-frame context via CDP
// Intercepts WeixinJSBridge to capture wx.request and other API calls
(function() {
    if (typeof __bridgeInjected !== 'undefined') return;
    window.__bridgeInjected = true;

    function report(type, data) {
        try {
            console.log('__BR__' + JSON.stringify({type: type, data: data, ts: Date.now()}));
        } catch(e) {}
    }

    if (typeof WeixinJSBridge === 'undefined') {
        report('error', {msg: 'no WeixinJSBridge'});
        return;
    }

    // Intercept invoke (sends commands to service layer)
    var origInvoke = WeixinJSBridge.invoke;
    if (origInvoke) {
        WeixinJSBridge.invoke = function(name, data, callback) {
            // capture network requests
            if (name === 'request' || name === 'downloadFile' || name === 'uploadFile' || name === 'connectSocket') {
                report('invoke', {
                    api: name,
                    url: data && data.url ? data.url : '',
                    method: data && data.method ? data.method : 'GET',
                    data: data && data.data ? (typeof data.data === 'string' ? data.data.substring(0, 512) : JSON.stringify(data.data).substring(0, 512)) : null,
                    header: data && data.header ? data.header : {}
                });
            }
            // wrap callback to capture responses
            if (callback) {
                var wrappedCb = function(res) {
                    if (name === 'request' || name === 'downloadFile' || name === 'uploadFile') {
                        report('response', {
                            api: name,
                            url: data && data.url ? data.url : '',
                            status: res && res.statusCode ? res.statusCode : (res && res.errMsg && res.errMsg.includes('ok') ? 200 : 0),
                            statusText: res && res.errMsg ? res.errMsg : '',
                            data: res && res.data ? (typeof res.data === 'string' ? res.data.substring(0, 2048) : JSON.stringify(res.data).substring(0, 2048)) : null
                        });
                    }
                    callback(res);
                };
                return origInvoke.call(WeixinJSBridge, name, data, wrappedCb);
            }
            return origInvoke.call(WeixinJSBridge, name, data, callback);
        };
    }

    // Intercept on (receives events from service layer)
    var origOn = WeixinJSBridge.on;
    if (origOn) {
        WeixinJSBridge.on = function(name, callback) {
            var wrappedCb = function(res) {
                if (name === 'onRequestTaskStateChange' || name === 'onNetworkStatusChange') {
                    report('event', {name: name, data: JSON.stringify(res).substring(0, 512)});
                }
                callback(res);
            };
            return origOn.call(WeixinJSBridge, name, wrappedCb);
        };
    }

    report('ready', {bridge: 'WeixinJSBridge intercepted'});
})();

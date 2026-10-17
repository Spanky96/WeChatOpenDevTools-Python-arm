// Hook mmcronet in WeChat main process - captures wx.request at native level
var mmcronet = Process.findModuleByName("mmcronet");
if (!mmcronet) {
    send("[-] mmcronet not loaded");
} else {
    send("[+] mmcronet @ " + mmcronet.base);

    function ex(name) {
        try { return mmcronet.getExportByName(name); } catch(e) { return null; }
    }

    var initFn = ex("Cronet_UrlRequest_InitWithParams");
    var startFn = ex("Cronet_UrlRequest_Start");
    var onSucceeded = ex("Cronet_UrlRequestCallback_OnSucceeded");
    var onFailed = ex("Cronet_UrlRequestCallback_OnFailed");
    var onResponseStarted = ex("Cronet_UrlRequestCallback_OnResponseStarted");
    var respUrlGet = ex("Cronet_UrlResponseInfo_url_get");
    var respStatusGet = ex("Cronet_UrlResponseInfo_http_status_code_get");

    // Get URL from response info (shared helper)
    var getRespUrl = respUrlGet ? new NativeFunction(respUrlGet, 'pointer', ['pointer']) : null;
    var getRespStatus = respStatusGet ? new NativeFunction(respStatusGet, 'int32', ['pointer']) : null;

    // Hook InitWithParams: (self, engine, url_string, params, callback)
    // args[2] = Cronet_String url (char*)
    if (initFn) {
        Interceptor.attach(initFn, {
            onEnter: function(args) {
                try {
                    var url = args[2].readCString();
                    if (url) send("[req] " + url);
                } catch(e) {}
            }
        });
        send("[+] hooked InitWithParams");
    }

    // Hook Start: (self)
    if (startFn) {
        Interceptor.attach(startFn, {
            onEnter: function(args) {
                send("[start] request fired");
            }
        });
    }

    // Hook OnResponseStarted: (callback, request, info, redirectInfo)
    if (onResponseStarted && getRespUrl) {
        Interceptor.attach(onResponseStarted, {
            onEnter: function(args) {
                try {
                    var url = getRespUrl(args[2]).readCString();
                    var status = getRespStatus ? getRespStatus(args[2]) : '?';
                    send("[res-start] " + status + " " + url);
                } catch(e) {}
            }
        });
        send("[+] hooked OnResponseStarted");
    }

    // Hook OnSucceeded: (callback, request, info)
    if (onSucceeded && getRespUrl) {
        Interceptor.attach(onSucceeded, {
            onEnter: function(args) {
                try {
                    var url = getRespUrl(args[2]).readCString();
                    send("[res-done] " + url);
                } catch(e) {}
            }
        });
        send("[+] hooked OnSucceeded");
    }

    // Hook OnFailed: (callback, request, info, error)
    if (onFailed) {
        Interceptor.attach(onFailed, {
            onEnter: function(args) {
                send("[res-fail] request failed");
            }
        });
    }

    send("[+] cronet armed — interact with mini-program!");
}

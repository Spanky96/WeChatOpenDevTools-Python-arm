(function () {
    if (window !== window.top || !/^https?:$/.test(location.protocol)) return;
    if (window.chii || window.__wechatH5ChiiLoading) return;
    window.__wechatH5ChiiLoading = true;
    window.ChiiServerUrl = __H5_SERVER_URL__;
    var script = document.createElement('script');
    script.src = window.ChiiServerUrl + 'target.js';
    script.onerror = function () {
        window.__wechatH5ChiiLoading = false;
        script.remove();
    };
    (document.head || document.documentElement).appendChild(script);
})();

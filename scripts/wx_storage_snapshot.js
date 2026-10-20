// Return typed wx Storage values as JSON text for the CDP read-only adapter.
(function () {
  try {
    var info = wx.getStorageInfoSync(), entries = [], size = 0;
    var keys = info.keys || [];
    for (var i = 0; i < keys.length; i++) {
      var value = JSON.stringify(wx.getStorageSync(keys[i]));
      if (value === undefined) value = 'undefined';
      var entry = [String(keys[i]), value];
      size += JSON.stringify(entry).length + 1;
      if (size > 4 * 1024 * 1024)
        return JSON.stringify({ err: 'wx Storage snapshot exceeds 4 Mi characters; no partial values returned' });
      entries.push(entry);
    }
    return JSON.stringify({ entries: entries });
  } catch (error) {
    return JSON.stringify({ err: String(error) });
  }
})();

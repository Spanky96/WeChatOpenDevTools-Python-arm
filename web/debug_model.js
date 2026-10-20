(function (root) {
  'use strict';
  function eventTime(event) {
    return Number(event.agent_t ? event.agent_t / 1000 : event.t) || 0;
  }
  function bodyText(value) {
    if (value === undefined || value === null) return '';
    return typeof value === 'string' ? value : JSON.stringify(value);
  }
  function truncated(row) {
    return [row.request, row.response].some(event => {
      const capture = event && event.data && event.data.capture;
      return capture && Object.values(capture).some(meta => meta && meta.truncated);
    });
  }
  function duration(row) {
    const end = row.response || row.fail;
    return row.request && end ? Math.max(0, (eventTime(end) - eventTime(row.request)) * 1000) : null;
  }
  class DebugStore {
    constructor() { this.apps = new Map(); }
    app(appid) {
      if (!this.apps.has(appid)) this.apps.set(appid, { network: new Map(), console: [], bytes: 0, discarded: 0 });
      return this.apps.get(appid);
    }
    ingest(events) {
      for (const event of events) {
        const app = this.app(event.appid || '?'), data = event.data || {};
        if (['request', 'response', 'fail'].includes(event.type)) {
          const id = String(data.id || `${event.t}-${event.type}`);
          let row = app.network.get(id);
          if (!row) {
            row = { id, url: data.url || '', bytes: 0 };
            app.network.set(id, row);
          }
          const oldSize = row.bytes;
          row[event.type] = event;
          row.url = data.url || row.url;
          row.bytes = ['request', 'response', 'fail'].reduce((n, type) => n + (row[type] ? JSON.stringify(row[type]).length * 2 : 0), 0);
          app.bytes += row.bytes - oldSize;
          while (app.network.size > 500 || (app.bytes > 8 * 1024 * 1024 && app.network.size > 1)) {
            const first = app.network.keys().next().value;
            app.bytes -= app.network.get(first).bytes;
            app.network.delete(first);
            app.discarded++;
          }
        } else if (['console', 'error', 'route', 'eval_result'].includes(event.type)) {
          app.console.push(event);
          if (app.console.length > 1000) app.console.splice(0, app.console.length - 1000);
        }
      }
    }
    rows(appid, filters = {}) {
      return [...this.app(appid).network.values()].filter(row => {
        const request = (row.request || {}).data || {}, response = (row.response || {}).data || {};
        if (filters.query && !row.url.toLowerCase().includes(filters.query.toLowerCase())) return false;
        if (filters.method && (request.method || 'GET') !== filters.method) return false;
        if (filters.status === 'pending' && (row.response || row.fail)) return false;
        if (filters.status === 'error' && !row.fail && !(response.status >= 400)) return false;
        if (filters.status === 'truncated' && !truncated(row)) return false;
        return true;
      }).reverse();
    }
    clearNetwork(appid) { const app = this.app(appid); app.network.clear(); app.bytes = 0; app.discarded = 0; }
  }
  const model = { DebugStore, eventTime, bodyText, truncated, duration };
  if (typeof module === 'object' && module.exports) module.exports = model;
  else root.DebugModel = model;
})(typeof globalThis === 'object' ? globalThis : this);

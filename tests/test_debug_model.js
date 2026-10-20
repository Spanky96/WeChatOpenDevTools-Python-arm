const assert = require('node:assert/strict');
const { DebugStore, duration, truncated } = require('../web/debug_model.js');
const store = new DebugStore();
const request = { appid: 'app', type: 'request', t: 10, agent_t: 1000,
  data: { id: 'one', url: 'https://test.invalid/endpoint', method: 'POST' } };
store.ingest([request, { appid: 'other', type: 'request', t: 10, data: { id: 'one' } },
  { appid: 'app', type: 'response', t: 15, agent_t: 1125,
    data: { id: 'one', status: 500, capture: { body: { truncated: true } } } }]);
assert.equal(store.app('app').network.size, 1, 'request and response must share a row');
const row = store.rows('app')[0];
assert.equal(duration(row), 125, 'duration must use capture time instead of arrival time');
assert.equal(truncated(row), true);
assert.equal(store.rows('app', { method: 'GET' }).length, 0);
assert.equal(store.rows('app', { status: 'pending' }).length, 0);
assert.equal(store.rows('app', { status: 'error' }).length, 1);
assert.equal(store.rows('app', { status: 'truncated' }).length, 1);
assert.equal(store.rows('app', { query: 'missing' }).length, 0);
for (let index = 0; index < 600; index++) store.ingest([{ ...request, data: { ...request.data, id: String(index) } }]);
assert.equal(store.app('app').network.size, 500);
assert.ok(store.app('app').discarded > 0);
store.clearNetwork('app');
assert.equal(store.app('app').bytes, 0);
assert.equal(store.app('other').network.size, 1);
console.log('Dashboard model regressions passed: correlation, timing, filters, retention.');

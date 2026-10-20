const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../scripts/wx_storage_snapshot.js'), 'utf8');
const values = new Map(Array.from({ length: 150 }, (_, index) => ['key-' + index, index]));
values.set('__proto__', { safe: true });
values.set('long', 'x'.repeat(5000));
values.set('numeric-string', '123');
function snapshot() {
  return JSON.parse(vm.runInNewContext(source, { wx: {
    getStorageInfoSync() { return { keys: [...values.keys()] }; },
    getStorageSync(key) { return values.get(key); },
  } }));
}
const result = snapshot();
assert.equal(result.entries.length, 153);
const entries = new Map(result.entries);
assert.deepEqual(JSON.parse(entries.get('__proto__')), { safe: true });
assert.equal(JSON.parse(entries.get('long')).length, 5000);
assert.equal(JSON.parse(entries.get('numeric-string')), '123');
assert.equal(JSON.parse(entries.get('key-123')), 123);
values.set('large', 'x'.repeat(4 * 1024 * 1024));
assert.match(snapshot().err, /exceeds/);
assert.equal(snapshot().entries, undefined, 'oversize snapshots must not silently return partial values');
console.log('wx Storage snapshot passed: all keys, long values, special keys, types, explicit size limit.');

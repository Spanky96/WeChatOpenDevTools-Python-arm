const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../scripts/direct_debug_probe.js'), 'utf8');
const base = 0x100000000;
const loadOffset = 0x5722e00, gateOffset = 0x92f7ea8;
const filterOffset = 0x92f4720, filterBranchOffset = 0x92f476c;
const fingerprints = new Map([
  [loadOffset, 'ff0306d1f65715a9f44f16a9fd7b17a9fdc30591f40301aa'],
  [gateOffset, 'ffc307d1fa671aa9f85f1ba9f6571ca9f44f1da9fd7b1ea9fd830791f40301aa'],
  [filterOffset, 'fc6fbaa9fa6701a9f85f02a9f65703a9f44f04a9fd7b05a9fd430191ff8308d1'],
]);

function fixture(options = {}) {
  const memory = new Map(), handlers = new Map(), hooks = new Map(), logs = [], writes = [];
  let onUnload;
  let now = 1000;
  class Pointer {
    constructor(value) { this.value = value; }
    add(n) { return new Pointer(this.value + n); }
    isNull() { return this.value === 0; }
    compare(other) { return Math.sign(this.value - other.value); }
    equals(other) { return this.value === other.value; }
    toInt32() { return this.value | 0; }
    toString() { return String(this.value); }
    readPointer() { return new Pointer(memory.get(this.value) || 0); }
    readU32() { return memory.get(this.value) || 0; }
    writeU32(value) { writes.push({address: this.value, value}); memory.set(this.value, value); }
    readByteArray(size) {
      const offset = this.value - base;
      return offset === options.badFingerprint
        ? new Uint8Array(size).buffer : Buffer.from(fingerprints.get(offset), 'hex');
    }
  }
  const pointer = value => new Pointer(value);
  const manager = pointer(base + 0x1000), state = base + 0x2000;
  const app = base + 0x3000, config = base + 0x4000, scene = config + 0x1c8;
  const container = pointer(base + 0x5000), containerState = base + 0x6000;
  memory.set(container.value + 0x38, containerState);
  memory.set(containerState + 0x610, manager.value);
  memory.set(manager.value + 8, state);
  memory.set(state + 0x5d0, app);
  memory.set(app + 0x10, config);
  memory.set(scene, 1005);
  memory.set(base + filterBranchOffset, options.badBranch ? 0 : 0x54000200);
  const context = vm.createContext({
    Date: {now: () => now},
    send: message => logs.push(message), rpc: {}, ptr: pointer,
    Memory: {patchCode: (address, size, callback) => callback(address)},
    recv: (type, callback) => handlers.set(type, callback),
    Process: {
      arch: 'arm64', pointerSize: 8,
      findModuleByName: () => ({base: pointer(base), size: 0x10000000}),
      findRangeByAddress: address => options.unmapped && address.value === scene ? null
        : {base: pointer(base), size: 0x10000000,
          protection: options.readOnly && address.value === scene ? 'r--' : 'rwx'},
    },
    Interceptor: {attach(address, callbacks) {
      const hook = {callbacks, detached: false, detach() { this.detached = true; }};
      hooks.set(address.value - base, hook);
      return hook;
    }},
    Script: {bindWeak(_, callback) { onUnload = callback; }},
  });
  vm.runInContext(source, context);
  return {
    hooks, logs, writes, context,
    getScene: () => memory.get(scene), setScene: value => memory.set(scene, value),
    enable: (value, forceLoad = false, bypassFilter = false) => handlers.get('enable')({enabled: value, force_load: forceLoad, bypass_filter: bypassFilter}),
    connected: value => handlers.get('connection_state')({connected: value}),
    advance: milliseconds => { now += milliseconds; },
    getFilter: () => memory.get(base + filterBranchOffset),
    setFilter: value => memory.set(base + filterBranchOffset, value),
    load(mainFrame = false) {
      const args = [container, pointer(mainFrame ? 1 : 0)];
      hooks.get(loadOffset).callbacks.onEnter.call({}, args);
      return args[1].toInt32();
    },
    enter() {
      const invocation = {};
      hooks.get(gateOffset).callbacks.onEnter.call(invocation, [manager]);
      return invocation;
    },
    leave(invocation) { hooks.get(gateOffset).callbacks.onLeave.call(invocation); },
    unload: () => onUnload(),
  };
}

{
  const f = fixture();
  assert.deepEqual([...f.hooks.keys()], [loadOffset, gateOffset]);
  f.leave(f.enter());
  assert.equal(f.getScene(), 1005);
  assert.equal(f.writes.length, 0, 'loading the probe must stay read-only');
  f.enable('true');
  f.leave(f.enter());
  assert.equal(f.writes.length, 0, 'only boolean true opts in');
  f.enable(true);
  for (const scene of [1005, 1007, 1008, 1027, 1035, 1053, 1074, 1145, 1178, 1256, 1260, 1302, 1308]) {
    f.setScene(scene);
    const invocation = f.enter();
    assert.equal(f.getScene(), 1101);
    f.leave(invocation);
    assert.equal(f.getScene(), scene, 'onLeave must restore the original scene');
  }
  for (const scene of [1000, 1001, 1101, 0xffffffff]) {
    f.setScene(scene);
    const writes = f.writes.length;
    f.leave(f.enter());
    assert.equal(f.writes.length, writes, 'scenes outside the whitelist must remain untouched');
  }
  f.setScene(1005);
  const invocation = f.enter();
  f.setScene(1260);
  f.leave(invocation);
  assert.equal(f.getScene(), 1260, 'a concurrent scene change must not be overwritten');
}

for (const cleanup of ['disable', 'rpc', 'unload']) {
  const f = fixture();
  f.enable(true);
  const invocation = f.enter();
  assert.equal(f.getScene(), 1101);
  if (cleanup === 'disable') f.enable(false);
  else if (cleanup === 'rpc') f.context.rpc.exports.dispose();
  else f.unload();
  assert.equal(f.getScene(), 1005, `${cleanup} must restore an in-flight write`);
  f.leave(invocation);
  if (cleanup === 'disable') {
    f.leave(f.enter());
    assert.equal(f.getScene(), 1005);
  } else {
    assert([...f.hooks.values()].every(hook => hook.detached));
    f.context.rpc.exports.dispose();
  }
}

{
  const f = fixture();
  assert.equal(f.load(), 0, 'the default probe must not change the load argument');
  f.enable(true);
  assert.equal(f.load(), 0, 'scene mode alone must not force the load branch');
  f.enable(true, true);
  assert.equal(f.load(), 1, 'explicit load experiment applies to listed scenes');
  for (const scene of [1000, 1001, 1101, 0xffffffff]) {
    f.setScene(scene);
    assert.equal(f.load(), 0, 'unknown and debug scenes retain the original load argument');
  }
  f.setScene(1005);
  f.enable(false, true);
  assert.equal(f.load(), 0, 'disabling the probe also disables the load override');
  assert.equal(f.writes.length, 0, 'load override only changes the current call argument');
}

for (const badFingerprint of [loadOffset, gateOffset, filterOffset]) {
  const f = fixture({badFingerprint});
  assert.equal(f.hooks.size, 0, 'both fingerprints must pass before any hook is installed');
  assert(f.logs.some(message => message.reason === 'fingerprint_mismatch'));
  assert.equal(f.writes.length, 0);
}

{
  const f = fixture();
  f.enable(true, true, true);
  f.connected(true);
  assert.equal(f.load(), 0, 'page navigation must reuse an existing native connection');
  f.leave(f.enter());
  assert.equal(f.getScene(), 1005, 'the gate must not recreate an active connection');
  f.advance(4000);
  f.connected(true);
  f.advance(4000);
  assert.equal(f.load(), 0, 'daemon heartbeats renew the active connection guard');
  f.connected(false);
  assert.equal(f.load(), 1, 'a disconnected transport can connect on the next load');
  f.connected(true);
  f.advance(5001);
  assert.equal(f.load(), 1, 'an expired guard cannot prevent recovery after a daemon failure');
}

for (const cleanup of ['disable', 'rpc', 'unload']) {
  const f = fixture();
  f.enable(true);
  assert.equal(f.getFilter(), 0x54000200, 'filter bypass requires explicit opt-in');
  f.enable(true, true, true);
  assert.equal(f.getFilter(), 0xd503201f);
  if (cleanup === 'disable') f.enable(false);
  else if (cleanup === 'rpc') f.context.rpc.exports.dispose();
  else f.unload();
  assert.equal(f.getFilter(), 0x54000200, `${cleanup} restores the original ARM64 branch`);
}
{
  const f = fixture({badBranch: true});
  assert.equal(f.hooks.size, 0);
  assert(f.logs.some(message => message.reason === 'fingerprint_mismatch'));
}
{
  const f = fixture();
  f.enable(true, true, true);
  f.setFilter(0x12345678);
  f.context.rpc.exports.dispose();
  assert.equal(f.getFilter(), 0x12345678, 'cleanup must not overwrite another code patch');
}

for (const options of [{unmapped: true}, {readOnly: true}]) {
  const f = fixture(options);
  f.enable(true);
  f.leave(f.enter());
  assert.equal(f.writes.length, 0, 'scene writes require readable and writable mapped memory');
}

console.log('direct_debug_probe regression tests passed');

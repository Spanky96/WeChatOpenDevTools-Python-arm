'use strict';

let moduleInfo;
let profile;
let vtable;
let source;
let thunk;
let descriptor;
let calloc;
let perform;
let wake;
let loop;
let mode;
let blockClass;
let cage;
const heapMask = ptr('0xfffffff800000000');
const records = new Map();
const maxFrames = 128;
const maxScanBytes = 768 * 1024 * 1024;

function inspectFrame(frame) {
    try {
        if (!frame.readPointer().equals(vtable)) return null;
        const coreRaw = frame.add(0x34).readU32();
        const core = ptr(coreRaw | 0).shl(3).and(cage);
        if (core.isNull() || !core.and(heapMask).equals(frame.and(heapMask))) return null;
        if (!core.readPointer().equals(moduleInfo.base.add(profile.core_frame_vtable))) return null;
        const windowRaw = core.add(0x28).readU32();
        const domWindow = ptr(windowRaw | 0).shl(3).and(cage);
        if (domWindow.isNull() || !domWindow.and(heapMask).equals(core.and(heapMask)) ||
            !domWindow.readPointer().equals(moduleInfo.base.add(profile.dom_window_vtable))) return null;
        return {address: frame.toString(), coreRaw, windowRaw};
    } catch (_) {
        return null;
    }
}

function initialize(config, code) {
    if (source) throw new Error('Already initialized');
    if (Process.arch !== 'arm64') throw new Error('Only the pinned ARM64 build is supported');
    profile = config;
    moduleInfo = Process.getModuleByName(profile.framework);
    const execute = moduleInfo.base.add(profile.execute_script);
    vtable = moduleInfo.base.add(profile.frame_vtable);
    cage = moduleInfo.base.add(profile.pointer_cage_mask).readPointer();
    const expected = profile.execute_fingerprint;
    if (Memory.scanSync(execute, expected.split(' ').length, expected).length !== 1 ||
        !vtable.add(profile.execute_slot).readPointer().equals(execute)) {
        throw new Error('ExecuteScript/vtable fingerprint mismatch; refusing injection');
    }
    if (!/^[\x00-\x7f]*$/.test(code) || code.length > 1024 * 1024)
        throw new Error('Expected ASCII source, at most 1 MiB');
    const allocator = Process.getModuleByName('libsystem_malloc.dylib');
    calloc = new NativeFunction(allocator.getExportByName('calloc'), 'pointer', ['ulong', 'ulong']);
    function alloc(size) {
        const p = calloc(1, size);
        if (p.isNull()) throw new Error('Native allocation failed');
        return p;
    }
    // Static StringImpl and native blocks survive detach. V8 can retain the source.
    // One source/thunk per attachment, at most 128 blocks; all expire with the renderer.
    const impl = alloc(code.length + 13);
    impl.writeU32(1);
    impl.add(4).writeU32(code.length);
    impl.add(8).writeU32(3); // Is8Bit | IsStatic (Chromium 144 release layout).
    impl.add(12).writeUtf8String(code);
    source = alloc(0x100);
    source.writePointer(impl);
    descriptor = alloc(16);
    descriptor.add(8).writeU64(104);
    blockClass = Process.getModuleByName('libsystem_blocks.dylib').getExportByName('_NSConcreteGlobalBlock');
    const kernel = Process.getModuleByName('libsystem_kernel.dylib');
    const mmap = new NativeFunction(kernel.getExportByName('mmap'), 'pointer',
        ['pointer', 'ulong', 'int', 'int', 'int', 'int64']);
    thunk = mmap(NULL, Process.pageSize, 3, 0x1002, -1, 0);
    if (thunk.equals(ptr(-1))) throw new Error('Native thunk mmap failed');
    if (!Memory.protect(thunk, Process.pageSize, 'r-x')) throw new Error('Native thunk protection failed');
    // CFRunLoop invokes native code only. No pending block references a Frida callback.
    Memory.patchCode(thunk, 512, p => {
        const w = new Arm64Writer(p, {pc: thunk});
        w.putPushRegReg('x29', 'x30');
        w.putPushRegReg('x19', 'x20');
        w.putMovRegReg('x19', 'x0');
        w.putLdrRegRegOffset('w9', 'x19', 56);
        w.putCbnzRegLabel('w9', 'return');
        w.putLdrRegRegOffset('x0', 'x19', 32);
        w.putLdrRegRegOffset('x9', 'x0', 0);
        w.putLdrRegRegOffset('x10', 'x19', 64);
        w.putInstruction(0xeb0a013f); // cmp x9, x10
        w.putBCondLabel('ne', 'invalid');
        w.putLdrswRegRegOffset('x9', 'x0', 0x34);
        w.putCbzRegLabel('w9', 'invalid');
        w.putLdrRegRegOffset('w10', 'x19', 72);
        w.putInstruction(0x6b0a013f); // cmp w9, w10: reject a replaced core frame.
        w.putBCondLabel('ne', 'invalid');
        w.putLdrRegRegOffset('x11', 'x19', 80);
        w.putInstruction(0x8a090d69); // and x9, x11, x9, lsl #3
        w.putLdrRegRegOffset('x10', 'x9', 0);
        w.putLdrRegRegOffset('x12', 'x19', 88);
        w.putInstruction(0xeb0c015f); // cmp x10, x12
        w.putBCondLabel('ne', 'invalid');
        w.putLdrswRegRegOffset('x10', 'x9', 0x28);
        w.putLdrRegRegOffset('w12', 'x19', 76);
        w.putInstruction(0x6b0c015f); // cmp w10, w12: reject a replaced DOM window.
        w.putBCondLabel('ne', 'invalid');
        w.putCbzRegLabel('w10', 'invalid');
        w.putInstruction(0x8a0a0d6a); // and x10, x11, x10, lsl #3
        w.putLdrRegRegOffset('x10', 'x10', 0);
        w.putLdrRegRegOffset('x12', 'x19', 96);
        w.putInstruction(0xeb0c015f); // cmp x10, x12
        w.putBCondLabel('ne', 'invalid');
        w.putLdrRegU32('w9', 1);
        w.putStrRegRegOffset('w9', 'x19', 56);
        w.putLdrRegRegOffset('x1', 'x19', 40);
        w.putLdrRegRegOffset('x16', 'x19', 48);
        w.putBlrReg('x16');
        w.putLdrRegU32('w9', 2);
        w.putStrRegRegOffset('w9', 'x19', 56);
        w.putBLabel('return');
        w.putLabel('invalid');
        w.putLdrRegU32('w9', 4);
        w.putStrRegRegOffset('w9', 'x19', 56);
        w.putLabel('return');
        w.putPopRegReg('x19', 'x20');
        w.putPopRegReg('x29', 'x30');
        w.putRet();
        w.flush();
    });
    const cf = Process.getModuleByName('CoreFoundation');
    loop = new NativeFunction(cf.getExportByName('CFRunLoopGetMain'), 'pointer', [])();
    perform = new NativeFunction(cf.getExportByName('CFRunLoopPerformBlock'), 'void', ['pointer', 'pointer', 'pointer']);
    wake = new NativeFunction(cf.getExportByName('CFRunLoopWakeUp'), 'void', ['pointer']);
    mode = cf.getExportByName('kCFRunLoopCommonModes').readPointer();
    return {pid: Process.id, path: moduleInfo.path};
}

function scan(schedule) {
    if (!source) throw new Error('Not initialized');
    const frames = [];
    let bytes = 0;
    let limited = false;
    let skipped = 0;
    for (const range of Process.enumerateRanges({protection: 'rw-', coalesce: false})) {
        if (range.file || range.size > 64 * 1024 * 1024) continue;
        if (bytes + range.size > maxScanBytes) { limited = true; break; }
        bytes += range.size;
        let hits;
        try {
            hits = Memory.scanSync(range.base, range.size, vtable.toMatchPattern());
        } catch (_) {
            skipped++;
            continue;
        }
        for (const hit of hits) {
            const frame = inspectFrame(hit.address);
            if (!frame) continue;
            frames.push(frame);
            if (frames.length >= maxFrames) { limited = true; break; }
        }
        if (limited) break;
    }
    let scheduled = 0;
    if (schedule === false) return {frames: frames.length, addresses: frames.map(f => f.address), bytes, limited};
    for (const {address, coreRaw, windowRaw} of frames) {
        let record = records.get(address);
        if (!record) {
            if (records.size >= maxFrames) { limited = true; break; }
            record = {block: calloc(1, 104), runs: 0};
            if (record.block.isNull()) throw new Error('Block allocation failed');
            const b = record.block;
            b.writePointer(blockClass);
            b.add(8).writeU32(1 << 28);
            b.add(16).writePointer(thunk);
            b.add(24).writePointer(descriptor);
            b.add(32).writePointer(ptr(address));
            b.add(40).writePointer(source);
            b.add(48).writePointer(moduleInfo.base.add(profile.execute_script));
            b.add(64).writePointer(vtable);
            b.add(80).writePointer(cage);
            b.add(88).writePointer(moduleInfo.base.add(profile.core_frame_vtable));
            b.add(96).writePointer(moduleInfo.base.add(profile.dom_window_vtable));
            records.set(address, record);
        } else if (record.block.add(56).readU32() < 2) {
            continue;
        }
        record.block.add(72).writeU32(coreRaw);
        record.block.add(76).writeU32(windowRaw);
        record.block.add(56).writeU32(0);
        perform(loop, mode, record.block);
        record.runs++;
        scheduled++;
    }
    if (scheduled) wake(loop);
    return {frames: frames.length, scheduled, bytes, limited, skipped};
}

rpc.exports = {
    identity(name) {
        const m = Process.getModuleByName(name);
        return {pid: Process.id, arch: Process.arch, path: m.path};
    },
    initialize,
    scan,
    states() {
        return Array.from(records, ([frame, r]) => ({frame, state: r.block.add(56).readU32(), runs: r.runs}));
    },
    stop() {
        for (const {block} of records.values()) {
            if (block.add(56).readU32() === 0) block.add(56).writeU32(3);
        }
    }
};

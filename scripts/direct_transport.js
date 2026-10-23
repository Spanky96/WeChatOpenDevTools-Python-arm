// Direct transport hooks for WeChat 4.1.13.98 / AppEx 2.5.6.25561 ARM64.
// Read-only until post({type: 'enable', enabled: true}); dispose restores writes.
(function () {
    'use strict';

    const previous = globalThis.__REA_DIRECT_PROBE__;
    if (previous) previous.dispose();
    const hooks = [];
    const patches = new Map();
    const allowedScenes = new Set([
        // 1000 is the multitab / in-WeChat web page scene. It uses the same
        // renderer pool as mini-programs but has no appid. Without allowing
        // it through the native debug gate, render-type=1 opens no CDP link.
        1000, 1005, 1007, 1008, 1027, 1035, 1053, 1074, 1145, 1178, 1256,
        1260, 1302, 1308
    ]);
    let enabled = false;
    let forceLoad = false;
    let filterBranch = null;
    let filterPatched = false;
    let disposed = false;
    let connectionLeaseUntil = 0;

    function connectionAlive() {
        return Date.now() < connectionLeaseUntil;
    }

    function log(event, hook, details) {
        try { send(Object.assign({type: 'direct_probe', event: event, hook: hook}, details)); }
        catch (_) { }
    }

    function mapped(address, size, permission) {
        if (!address || address.isNull()) return false;
        const range = Process.findRangeByAddress(address);
        return !!range && range.protection.includes(permission)
            && address.compare(range.base) >= 0
            && address.add(size).compare(range.base.add(range.size)) <= 0;
    }

    function pointerAt(address) {
        if (!mapped(address, 8, 'r')) throw new Error('unreadable_pointer');
        const value = address.readPointer();
        if (value.isNull()) throw new Error('null_pointer');
        return value;
    }

    function scenePointer(manager) {
        const state = pointerAt(manager.add(8));
        const app = pointerAt(state.add(0x5d0));
        const config = pointerAt(app.add(0x10));
        const scene = config.add(0x1c8);
        if (!mapped(scene, 4, 'r')) throw new Error('unreadable_scene');
        return scene;
    }

    function restore(patch) {
        try {
            if (mapped(patch.address, 4, 'r') && mapped(patch.address, 4, 'w')
                    && scenePointer(patch.manager).equals(patch.address)
                    && patch.address.readU32() === 1101) {
                patch.address.writeU32(patch.scene);
                return true;
            }
        } catch (_) { }
        return false;
    }

    function restoreAll() {
        for (const patch of patches.values()) {
            log('scene', 'gate', {scene: patch.scene, restored: restore(patch)});
        }
        patches.clear();
    }

    function setFilter(bypass) {
        if (bypass && !filterPatched) {
            if (filterBranch.readU32() !== 0x54000200) throw new Error('filter_branch_changed');
            Memory.patchCode(filterBranch, 4, code => code.writeU32(0xd503201f));
            filterPatched = true;
        } else if (!bypass && filterPatched) {
            if (filterBranch.readU32() === 0xd503201f) {
                Memory.patchCode(filterBranch, 4, code => code.writeU32(0x54000200));
            }
            filterPatched = false;
        }
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        enabled = false;
        forceLoad = false;
        connectionLeaseUntil = 0;
        setFilter(false);
        restoreAll();
        for (const hook of hooks) {
            try { hook.detach(); } catch (_) { }
        }
        hooks.length = 0;
        log('exit', 'probe', {reason: 'disposed'});
    }

    const lifetime = {dispose: dispose};
    globalThis.__REA_DIRECT_PROBE__ = lifetime;
    Script.bindWeak(lifetime, dispose);
    rpc.exports = {dispose: dispose};

    function fingerprint(module, offset, hex) {
        const address = module.base.add(offset);
        if (address.add(hex.length / 2).compare(module.base.add(module.size)) > 0
                || !mapped(address, hex.length / 2, 'r')
                || !mapped(address, hex.length / 2, 'x')) return false;
        const bytes = new Uint8Array(address.readByteArray(hex.length / 2));
        return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('') === hex;
    }

    try {
        if (Process.arch !== 'arm64' || Process.pointerSize !== 8) throw new Error('unsupported_arch');
        const module = Process.findModuleByName('WeChatAppEx Framework');
        if (!module) throw new Error('module_missing');
        if (!fingerprint(module, 0x5722e00, 'ff0306d1f65715a9f44f16a9fd7b17a9fdc30591f40301aa')
                || !fingerprint(module, 0x92f7ea8,
                    'ffc307d1fa671aa9f85f1ba9f6571ca9f44f1da9fd7b1ea9fd830791f40301aa')
                || !fingerprint(module, 0x92f4720,
                    'fc6fbaa9fa6701a9f85f02a9f65703a9f44f04a9fd7b05a9fd430191ff8308d1')) {
            throw new Error('fingerprint_mismatch');
        }
        filterBranch = module.base.add(0x92f476c);
        if (!mapped(filterBranch, 4, 'r') || !mapped(filterBranch, 4, 'x')
                || filterBranch.readU32() !== 0x54000200) throw new Error('fingerprint_mismatch');

        hooks.push(Interceptor.attach(module.base.add(0x5722e00), {
            onEnter: function (args) {
                const mainFrame = args[1].toInt32() !== 0;
                const details = {main_frame: mainFrame, forced: false};
                if (enabled && !mainFrame) {
                    try {
                        const state = pointerAt(args[0].add(0x38));
                        const manager = pointerAt(state.add(0x610));
                        const scene = scenePointer(manager).readU32();
                        details.scene = scene;
                        details.connection_reused = connectionAlive();
                        if (forceLoad && !connectionAlive() && allowedScenes.has(scene)) {
                            args[1] = ptr(1);
                            details.forced = true;
                        }
                    } catch (_) { details.reason = 'unavailable'; }
                }
                log('enter', 'OnLoadStart', details);
            },
            onLeave: function () { log('exit', 'OnLoadStart'); }
        }));
        hooks.push(Interceptor.attach(module.base.add(0x92f7ea8), {
            onEnter: function (args) {
                log('enter', 'gate');
                try {
                    const address = scenePointer(args[0]);
                    const scene = address.readU32();
                    const key = address.toString();
                    const change = enabled && !connectionAlive() && allowedScenes.has(scene)
                        && patches.size < 16 && !patches.has(key) && mapped(address, 4, 'w');
                    if (change) {
                        const patch = {address: address, manager: args[0], scene: scene};
                        patches.set(key, patch);
                        this.patch = patch;
                        address.writeU32(1101);
                    }
                    log('scene', 'gate', {scene: scene, changed: change});
                } catch (_) {
                    log('scene', 'gate', {reason: 'unavailable'});
                }
            },
            onLeave: function () {
                if (this.patch) {
                    const key = this.patch.address.toString();
                    if (patches.get(key) === this.patch) {
                        log('scene', 'gate', {scene: this.patch.scene, restored: restore(this.patch)});
                        patches.delete(key);
                    }
                }
                log('exit', 'gate');
            }
        }));
        log('enter', 'probe', {enabled: false});
        recv('enable', function onEnable(message) {
            if (disposed) return;
            enabled = message.enabled === true;
            forceLoad = enabled && message.force_load === true;
            try { setFilter(enabled && message.bypass_filter === true); }
            catch (error) { log('exit', 'probe', {reason: error.message}); dispose(); return; }
            if (!enabled) restoreAll();
            log('scene', 'probe', {enabled: enabled, force_load: forceLoad, bypass_filter: filterPatched});
            recv('enable', onEnable);
        });
        recv('dispose', dispose);
        recv('connection_state', function onConnectionState(message) {
            if (disposed) return;
            // The daemon renews this lease; a stopped transport cannot leave a stale guard.
            connectionLeaseUntil = message.connected === true ? Date.now() + 5000 : 0;
            recv('connection_state', onConnectionState);
        });
    } catch (error) {
        log('exit', 'probe', {reason: error.message});
        dispose();
    }
})();

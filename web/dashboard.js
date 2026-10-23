'use strict';
const $ = id => document.getElementById(id);
const { DebugStore, eventTime, bodyText, truncated, duration } = DebugModel;
const store = new DebugStore();
const state = { selected: null, status: { targets: [], mps: {} }, tab: 'network',
  selectedH5: null,
  cursor: null, since: Date.now() / 1000 - 300, netSel: null, detailTab: 'headers',
  storage: {}, storageLoaded: new Set(), files: {}, source: null, bps: new Map(), pendingBps: new Set(),
  consolePaused: false, frozenConsole: [], firstSeen: new Map(), sync: '等待同步',
  settings: { request_body: 32768, response_body: 131072, headers: 16384 } };
const phases = { discovered: '等待发现', attaching: '附加目标', waiting_context: '等待上下文',
  injecting: '注入中', connecting: '直连中', online: '在线', offline: '离线', retrying: '等待重试' };
let toastTimer, pollTimer, polling = false, consoleSignature = '', networkSignature = '', appsSignature = '';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const icon = name => `<i data-lucide="${name}"></i>`;
function icons() { if (window.lucide) lucide.createIcons(); }
function ts(time) { return time ? new Date(time * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '--'; }
function ago(time) { if (!time) return '尚无事件'; const seconds = Math.max(0, Math.floor(Date.now() / 1000 - time)); return seconds < 60 ? `${seconds} 秒前` : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : `${Math.floor(seconds / 3600)} 小时前`; }
function bytes(size) { return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`; }
function textSize(value) { return new TextEncoder().encode(bodyText(value)).length; }
function pretty(value) { if (value === undefined) return ''; if (typeof value === 'string') { try { return JSON.stringify(JSON.parse(value), null, 2); } catch (_) { return value; } } return JSON.stringify(value, null, 2); }
function parseHeaders(value) { if (typeof value === 'string') { try { value = JSON.parse(value); } catch (_) { return { '(已截断的原始头)': value }; } } return value && typeof value === 'object' ? value : {}; }
function toast(message, error = false) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').className = `toast${error ? ' error' : ''}`; toastTimer = setTimeout(() => $('toast').classList.add('hidden'), 3500); }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(24000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const value = await response.json();
  if (value.error) throw new Error(value.error);
  return value;
}
const post = (path, value) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
async function copy(value) { try { await navigator.clipboard.writeText(String(value ?? '')); toast('已复制'); } catch (_) { toast('复制失败', true); } }
function download(name, value, type = 'application/json') { const blob = new Blob([typeof value === 'string' ? value : JSON.stringify(value, null, 2)], { type }); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
function target() { return state.status.targets.find(item => item.appid === state.selected) || (state.selectedH5?.appid === state.selected ? state.selectedH5 : null); }
function currentStatus() { return (state.status.mps || {})[state.selected] || (state.selectedH5 ? { phase: 'offline' } : {}); }
function phaseName(status) { return phases[status.phase] || '等待发现'; }
function isWebTarget(item) { return item.kind === 'web' || String(item.appid || '').startsWith('web:'); }
function isChiiTarget(item) { return item?.transport === 'chii'; }
function targetName(item) { return isWebTarget(item) ? (item.title || '网页') : (item.appid || '小程序'); }
function hostname(url) { try { return new URL(url).hostname; } catch (_) { return ''; } }
function chiiUrl(item) {
  const url = new URL(item.devtools_url);
  const endpoint = new URL('ws://' + url.searchParams.get('ws'));
  endpoint.pathname = '/client/' + crypto.randomUUID();
  url.searchParams.set('ws', endpoint.host + endpoint.pathname + endpoint.search);
  return url.href;
}

function renderApps() {
  const targets = [...state.status.targets].filter(item => item.appid);
  for (const item of targets) if (!state.firstSeen.has(item.appid)) state.firstSeen.set(item.appid, state.firstSeen.size);
  targets.sort((a, b) => Number(isWebTarget(a)) - Number(isWebTarget(b)) || state.firstSeen.get(a.appid) - state.firstSeen.get(b.appid));
  $('app-count').textContent = targets.length;
  if (!targets.some(item => item.appid === state.selected)) {
    if (state.selectedH5) {
      const replacement = targets.find(item => isChiiTarget(item) && item.url === state.selectedH5.url);
      if (replacement) selectApp(replacement.appid, false);
      else targets.push(state.selectedH5);
    } else selectApp(targets[0]?.appid || null, false);
  }
  const liveSelection = targets.find(item => item.appid === state.selected);
  if (isChiiTarget(liveSelection)) state.selectedH5 = liveSelection;
  const itemHtml = item => {
    const status = (state.status.mps || {})[item.appid] || (isChiiTarget(item) ? { phase: 'offline' } : {});
    const version = (item.url.match(/\/wx[\da-f]+\/(\d+)\//) || [])[1];
    const web = isWebTarget(item);
    return `<button class="app-item${state.selected === item.appid ? ' selected' : ''}" data-app="${esc(item.appid)}" aria-pressed="${state.selected === item.appid}"><span class="app-item-top">${icon(web ? 'globe-2' : 'app-window')}${web ? esc(hostname(item.url) || '网页') : version === '0' ? '真机调试' : '小程序会话'}</span><span class="app-item-sub" title="${esc(targetName(item))}">${esc(targetName(item))}</span><span class="app-item-bottom"><b class="dot ${status.agent_online ? 'on' : status.phase === 'offline' ? 'off' : 'wait'}"></b>${phaseName(status)}<span class="app-release">${web ? (isChiiTarget(item) ? 'Chii' : 'CDP') : '版本 ' + esc(version || '--')}</span></span></button>`;
  };
  const mini = targets.filter(item => !isWebTarget(item));
  const web = targets.filter(isWebTarget);
  const group = (label, rows) => rows.length ? `<div class="app-group-title">${label}<span>${rows.length}</span></div>${rows.map(itemHtml).join('')}` : '';
  const html = group('小程序会话', mini) + (group('网页', web) || '<div class="app-group-title">网页<span>0</span></div><div class="group-state">等待页面</div>');
  if (appsSignature !== html) { appsSignature = html; $('apps').innerHTML = html; icons(); }
  for (const name of ['sniper', 'gateway', 'sess']) $('service-' + name).className = 'service ' + (state.status[name] ? 'on' : 'off');
  const h5 = state.status.h5 || {};
  $('service-h5').className = 'service ' + (h5.online ? 'on' : 'off');
  $('service-h5').title = h5.online ? 'Chii ' + h5.version : h5.reason || '网页服务未启动';
  $('h5-service-label').textContent = h5.reason || (h5.online ? `网页在线 · ${h5.targets?.length || 0}` : '网页服务未连接');
  $('h5-service-label').title = h5.reason || '';
  $('start-h5').hidden = !!h5.online;
  $('start-h5').disabled = false;
  $('start-h5').title = h5.reason || '启动网页服务';
  const bridgeUp = state.status.bridge_state === 'up';
  $('bridge-title').textContent = state.status.debug_mode === 'direct' ? '独立调试' : '调试桥';
  $('bridge-dot').className = 'dot ' + (bridgeUp ? 'on' : 'wait');
  $('bridge-port').textContent = state.status.bridge || '--';
  $('bridge-state').textContent = bridgeUp ? '已连接' : state.status.bridge_state === 'connecting' ? '连接中' : '未连接';
  renderSession();
}
function selectApp(appid, render = true) {
  if (appid === state.selected) return;
  state.selected = appid; state.netSel = null; state.source = null;
  const selectedTarget = state.status.targets.find(item => item.appid === appid);
  state.selectedH5 = isChiiTarget(selectedTarget) ? selectedTarget : null;
  state.frozenConsole = [...store.app(appid).console];
  networkSignature = consoleSignature = '';
  if (render) {
    renderApps(); renderActive(true);
    if (state.tab === 'storage') loadStorage();
    if (state.tab === 'source') loadFileList();
  }
}
function renderSession() {
  const status = currentStatus(), selected = !!state.selected;
  const selectedTarget = target();
  const web = selectedTarget ? isWebTarget(selectedTarget) : false;
  const chii = isChiiTarget(selectedTarget);
  $('selected-app').textContent = selectedTarget ? targetName(selectedTarget) : '未选择调试目标';
  $('session-phase').textContent = phaseName(status) + (status.retry_in > 0 ? ` · ${Math.ceil(status.retry_in)} 秒` : '');
  $('session-phase').className = 'status-pill' + (status.agent_online ? '' : status.phase === 'offline' ? ' offline' : ' pending');
  $('agent-version').textContent = chii ? 'Chii ' + (state.status.h5?.version || '1.15.5') : web ? '原生 CDP' : 'Agent ' + (status.agent_version ? 'v' + status.agent_version : '--');
  $('last-event').textContent = chii ? hostname(selectedTarget.url) : '最近事件 · ' + ago(status.last_event_at);
  $('open-devtools').disabled = !selectedTarget || (chii && !status.agent_online);
  $('open-devtools').innerHTML = icon('external-link') + (chii ? '独立窗口' : 'DevTools');
  $('retry-agent').innerHTML = icon('rotate-cw') + (chii ? '重连' : '重试连接');
  $('retry-agent').disabled = !selected || (!web && status.agent_online);
  $('settings-open').disabled = chii;
  $('repl-in').disabled = !selected;
  $('storage-tab').hidden = web;
  $('source-tab').hidden = web;
  if (web && ['storage', 'source'].includes(state.tab)) switchTab('network');
  const notice = status.capture_legacy?.length ? '当前会话仍使用旧捕获钩子，重新打开该小程序后，新捕获上限和截断标记才会生效。' : status.reason && (!status.agent_online || web) ? status.reason : '';
  $('session-notice').textContent = notice;
  $('session-notice').classList.toggle('hidden', !notice);
  const paused = status.paused;
  $('paused-bar').classList.toggle('hidden', chii || !paused);
  $('paused-reason').textContent = paused ? `${paused.frames?.[0]?.fn || ''} ${paused.frames?.[0]?.url || ''}:${paused.frames?.[0]?.line || ''}` : '';
  $('net-badge').textContent = store.app(state.selected).network.size;
  $('con-badge').textContent = store.app(state.selected).console.length;
  $('footer-activity').textContent = chii ? 'Chii · ' + selectedTarget.targetId : selected ? '最近事件 ' + ts(status.last_event_at) : '无活动会话';
  document.querySelector('.tabs').hidden = chii;
  document.querySelectorAll('.page').forEach(page => {
    const active = !chii && page.id === 'page-' + state.tab;
    page.classList.toggle('active', active); page.hidden = !active;
  });
  $('h5-workspace').hidden = !chii;
  if (chii) renderH5(selectedTarget, status);
  else if ($('h5-devtools').hasAttribute('src')) {
    $('h5-devtools').removeAttribute('src'); delete $('h5-devtools').dataset.target;
  }
  icons();
  if (state.tab === 'diagnostics') renderDiagnostics();
}
function switchTab(name) {
  if (['storage', 'source'].includes(name) && isWebTarget(target() || {})) name = 'network';
  state.tab = name;
  document.querySelectorAll('.tab').forEach(button => { const active = button.dataset.tab === name; button.classList.toggle('active', active); button.setAttribute('aria-selected', active); button.tabIndex = active ? 0 : -1; });
  document.querySelectorAll('.page').forEach(page => { const active = page.id === 'page-' + name; page.classList.toggle('active', active); page.hidden = !active; });
  renderActive(true);
  if (name === 'storage' && !state.storageLoaded.has(state.selected)) loadStorage();
  if (name === 'source') loadFileList();
}
function renderActive(force = false) {
  renderSession();
  if (isChiiTarget(target())) return;
  if (state.tab === 'network') renderNetwork(force);
  if (state.tab === 'console') renderConsole(force);
  if (state.tab === 'storage') renderStorage();
  if (state.tab === 'diagnostics') renderDiagnostics();
}

function renderH5(item, status) {
  const frame = $('h5-devtools'), h5 = state.status.h5 || {};
  const online = !!h5.online && !!status.agent_online;
  frame.hidden = !online;
  $('h5-empty').hidden = online;
  $('h5-empty-title').textContent = h5.online ? '等待页面重新连接' : '网页服务未连接';
  $('h5-empty-reason').textContent = h5.reason || '';
  const connection = item.appid + ':' + h5.port + ':' + item.connection_id;
  if (online && frame.dataset.target !== connection) {
    frame.src = chiiUrl(item);
    frame.dataset.target = connection;
  }
}

function filteredRows() { return store.rows(state.selected, { query: $('net-filter').value.trim(), method: $('net-method').value, status: $('net-state').value }); }
function renderNetwork(force = false) {
  const app = store.app(state.selected), rows = filteredRows();
  const signature = JSON.stringify([state.selected, state.netSel, app.bytes, rows.map(row => [row.id, row.response?.t, row.fail?.t])]);
  if (!force && networkSignature === signature) return;
  networkSignature = signature;
  $('net-count').textContent = `${rows.length} / ${app.network.size} 个请求`;
  $('summary-total').textContent = rows.length;
  $('summary-errors').textContent = rows.filter(row => row.fail || row.response?.data?.status >= 400).length;
  $('summary-size').textContent = bytes(rows.reduce((size, row) => size + textSize(row.response?.data?.data), 0));
  $('network-retention').textContent = app.discarded ? `${app.discarded} 条较早记录已移出视图` : '';
  $('net-empty').style.display = rows.length ? 'none' : 'flex';
  $('net-empty-label').textContent = app.network.size ? '没有匹配的请求' : currentStatus().agent_online ? '等待新请求' : '等待 agent 上线';
  $('net-rows').innerHTML = rows.map(row => {
    let name = row.url, host = '';
    try { const url = new URL(row.url); name = url.pathname.split('/').filter(Boolean).pop() || '/'; name += url.search; host = url.hostname; } catch (_) {}
    const request = row.request?.data || {}, response = row.response?.data || {};
    const elapsed = duration(row), code = row.fail ? '失败' : response.status || '等待';
    const statusClass = row.fail || response.status >= 400 ? 'error-text' : row.response ? 'success-text' : 'muted';
    return `<tr class="nrow${state.netSel === row.id ? ' selected' : ''}" data-request="${esc(row.id)}" tabindex="0" aria-label="${esc(name)}"><td><span class="status-code ${statusClass}">${esc(code)}</span></td><td title="${esc(row.url)}"><div class="request-name">${esc(name)}${truncated(row) ? '<span class="truncated-badge">截断</span>' : ''}</div><div class="request-domain">${esc(host)}</div></td><td class="method">${esc(request.method || '--')}</td><td class="size-cell">${row.response ? bytes(textSize(response.data)) : '--'}</td><td class="time-cell">${elapsed === null ? '--' : Math.round(elapsed) + ' ms'}</td></tr>`;
  }).join('');
  const row = app.network.get(state.netSel);
  $('net-detail').classList.toggle('hidden', !row);
  $('network-workspace').classList.toggle('has-detail', !!row);
  if (row) renderDetail(row);
}
function pairs(values) { return '<dl>' + Object.entries(values).map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(typeof value === 'object' ? JSON.stringify(value) : value)}</dd>`).join('') + '</dl>'; }
function warning(event, key) { const meta = event?.data?.capture?.[key]; return meta?.truncated ? `<div class="capture-warning">已截断 · 保留 ${meta.captured_chars} / ${meta.original_chars} 字符 · 上限 ${meta.limit}</div>` : ''; }
function renderDetail(row) {
  const box = $('net-detail'), scroll = box.querySelector('.detail-content')?.scrollTop || 0;
  const request = row.request?.data || {}, response = row.response?.data || {};
  const tabs = { headers: '请求头', payload: '参数', response: '响应', timing: '耗时' };
  let content;
  if (state.detailTab === 'headers') content = `<section><h3>概览</h3>${pairs({ 'URL': row.url, '方法': request.method || '--', '状态': row.fail ? '失败' : response.status || '等待响应', 'API': request.api || response.api || '--' })}</section><section><h3>请求头</h3>${warning(row.request, 'headers')}${pairs(parseHeaders(request.header))}</section><section><h3>响应头</h3>${warning(row.response, 'headers')}${pairs(parseHeaders(response.header))}</section>`;
  if (state.detailTab === 'payload') {
    let query = {};
    try { query = Object.fromEntries(new URL(row.url).searchParams); } catch (_) {}
    content = `<section><h3>Query String</h3>${Object.keys(query).length ? pairs(query) : '<span class="muted">无</span>'}</section><section><h3>请求体</h3>${warning(row.request, 'body')}<pre class="body-code">${esc(pretty(request.data) || '无')}</pre></section>`;
  }
  if (state.detailTab === 'response') content = row.fail ? `<section><h3>请求失败</h3><pre class="body-code error-text">${esc(pretty(row.fail.data.err))}</pre></section>` : row.response ? `${warning(row.response, 'body')}<pre class="body-code">${esc(pretty(response.data) || '响应体为空')}</pre>` : '<div class="empty">等待响应</div>';
  if (state.detailTab === 'timing') content = pairs({ '请求时间': ts(eventTime(row.request || row.response || row.fail)), '响应时间': row.response || row.fail ? ts(eventTime(row.response || row.fail)) : '--', '总耗时': duration(row) === null ? '--' : Math.round(duration(row)) + ' ms', '已捕获响应体': bytes(textSize(response.data)), '请求 ID': row.id });
  box.innerHTML = `<div class="detail-heading"><button class="icon-btn detail-back" data-detail-action="close" aria-label="返回列表" data-tip="返回列表">${icon('arrow-left')}</button><div class="detail-url">${esc(row.url)}</div><button class="icon-btn" data-detail-action="close" aria-label="关闭详情" data-tip="关闭详情">${icon('x')}</button></div><div class="detail-tools"><button class="icon-btn" data-detail-action="url" aria-label="复制 URL" data-tip="复制 URL">${icon('link')}</button><button class="btn" data-detail-action="curl">${icon('copy')}cURL</button><button class="icon-btn" data-detail-action="body" aria-label="复制响应体" data-tip="复制响应体">${icon('copy')}</button><button class="icon-btn" data-detail-action="download" aria-label="下载响应体" data-tip="下载响应体">${icon('download')}</button></div><div class="detail-tabs">${Object.entries(tabs).map(([key, label]) => `<button class="detail-tab${state.detailTab === key ? ' active' : ''}" data-detail-tab="${key}">${label}</button>`).join('')}</div><div class="detail-content">${content}</div>`;
  box.querySelector('.detail-content').scrollTop = scroll;
  icons();
}
function curlCommand(row) {
  const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
  const data = row.request?.data || {};
  let command = `curl ${quote(row.url)} -X ${quote(data.method || 'GET')}`;
  for (const [key, value] of Object.entries(parseHeaders(data.header))) command += ' -H ' + quote(key + ': ' + value);
  if (data.data !== undefined) command += ' --data-raw ' + quote(bodyText(data.data));
  return command;
}
function logValue(value) { return value && value.__err ? `${value.name}: ${value.message}\n${value.stack || ''}` : typeof value === 'string' ? value : JSON.stringify(value); }
function logText(event) { const data = event.data || {}; if (event.type === 'console') return (data.args || []).map(logValue).join(' '); if (event.type === 'error') return logValue(data.msg); if (event.type === 'route') return '页面 · ' + data.route; return logValue(data.data ?? data); }
function logLevel(event) { return event.type === 'error' ? 'error' : event.type === 'route' ? 'info' : event.data?.level || 'log'; }
function consoleRows() { const query = $('con-filter').value.toLowerCase(), level = $('con-level').value; return (state.consolePaused ? state.frozenConsole : store.app(state.selected).console).filter(event => { const text = logText(event) || ''; return !text.includes('8779/agent') && (!query || text.toLowerCase().includes(query)) && (!level || logLevel(event) === level); }); }
function renderConsole(force = false) {
  const rows = consoleRows(), box = $('console-scroll');
  const signature = JSON.stringify([state.selected, rows.length, rows.at(-1), $('con-filter').value, $('con-level').value]);
  if (!force && signature === consoleSignature) return;
  consoleSignature = signature;
  const scroll = box.scrollTop;
  $('con-count').textContent = `${rows.length} 条${state.consolePaused ? ' · 暂停显示' : ''}`;
  box.innerHTML = rows.map(event => `<div class="cline lv-${esc(logLevel(event))}"><span class="ts">${ts(eventTime(event))}</span><span class="log-content">${esc(logText(event)).replace(/([\w./-]+\.js):(\d+):(\d+)/g, (_, file, line, col) => `<a class="srclink" data-source-ref="${file}" data-line="${line}" data-col="${col}" tabindex="0">${file}:${line}:${col}</a>`)}</span></div>`).join('') || '<div class="empty">暂无匹配日志</div>';
  box.scrollTop = $('con-follow').checked && !state.consolePaused ? box.scrollHeight : scroll;
}
async function sendEval(event) {
  event.preventDefault();
  const input = $('repl-in'), code = input.value.trim(), appid = state.selected;
  if (!code || !appid) return;
  input.value = '';
  const add = (text, level) => store.ingest([{ appid, type: 'console', t: Date.now() / 1000, data: { level, args: [text] } }]);
  add('> ' + code, 'info'); renderConsole(true);
  try { const result = await post('/api/evaluate', { appid, code }); add(logValue(result.value) ?? 'undefined', result.ok ? 'info' : 'error'); } catch (error) { add(error.message, 'error'); }
  renderConsole(true);
}
async function loadStorage() {
  const appid = state.selected;
  if (!appid) return;
  $('st-info').textContent = '读取中'; $('storage-refresh').disabled = true;
  try {
    const result = await api('/api/storage?appid=' + appid), data = result.data || {};
    if (data.err) throw new Error(data.err);
    state.storage[appid] = data; state.storageLoaded.add(appid);
    if (state.selected === appid) renderStorage();
  } catch (error) { $('st-info').textContent = '读取失败'; toast(error.message, true); }
  finally { $('storage-refresh').disabled = false; }
}
function renderStorage() {
  const data = state.storage[state.selected] || {}, query = $('storage-filter').value.toLowerCase();
  const entries = Object.entries(data.kv || {}).filter(([key, value]) => (key + bodyText(value)).toLowerCase().includes(query));
  $('st-info').textContent = `${data.sz ?? data.currentSize ?? '--'} / ${data.limit ?? data.limitSize ?? '--'} KB · ${entries.length} keys`;
  $('st-rows').innerHTML = entries.map(([key, value]) => `<tr><td>${esc(key)}</td><td><pre>${esc(pretty(value))}</pre></td><td><button class="icon-btn" data-storage-copy="${esc(key)}" aria-label="复制 ${esc(key)}" data-tip="复制">${icon('copy')}</button></td></tr>`).join('');
  $('st-empty').style.display = entries.length ? 'none' : 'flex'; icons();
}

async function loadFileList() {
  const appid = state.selected;
  if (!appid) return;
  try {
    if (!state.files[appid]) state.files[appid] = (await api('/api/files?appid=' + appid)).files || [];
    if (state.selected !== appid) return;
    const files = state.files[appid], select = $('src-file');
    const previous = state.source?.appid === appid ? state.source.file : null;
    select.innerHTML = files.map(file => `<option value="${esc(file)}">${esc(file)}</option>`).join('') || '<option value="">暂无源码</option>';
    if (previous && files.includes(previous)) select.value = previous;
    if (select.value && !previous) await loadSource();
    else renderSource();
  } catch (error) { toast(error.message, true); }
}
async function loadSource(line) {
  const appid = state.selected, file = $('src-file').value;
  if (!appid || !file) return;
  $('src-info').textContent = '加载中';
  try {
    const result = await api(`/api/source?appid=${appid}&file=${encodeURIComponent(file)}`);
    if (appid !== state.selected || file !== $('src-file').value) return;
    state.source = { appid, file, lines: result.lines || [], formatted: false, pretty: null };
    renderSource(line);
  } catch (error) { $('src-info').textContent = error.message; }
}
function renderSource(highlight) {
  const source = state.source;
  if (!source) { $('src-scroll').innerHTML = '<div class="empty">暂无源码</div>'; return; }
  const lines = source.formatted ? source.pretty : source.lines;
  $('src-raw').classList.toggle('active', !source.formatted); $('src-format').classList.toggle('active', source.formatted);
  $('src-info').textContent = `${source.file} · ${lines.length} 行${source.formatted ? ' · 格式化视图不支持设置断点，行号与原始源码不同' : ''}`;
  const bps = state.bps.get(state.selected) || new Set();
  $('src-scroll').innerHTML = lines.map((text, index) => `<div class="srcline${index + 1 === Number(highlight) ? ' hl' : ''}${!source.formatted && bps.has(source.file + ':' + (index + 1)) ? ' bp' : ''}" id="L${index + 1}"><span class="no" data-breakpoint="${index + 1}">${index + 1}</span><span>${esc(text)}</span></div>`).join('');
  if (highlight) $('L' + Number(highlight))?.scrollIntoView({ block: 'center' });
}
async function toggleBreakpoint(line) {
  const source = state.source, appid = state.selected;
  if (!source) return;
  if (source.formatted) { toast('请在原始源码中设置断点'); return; }
  const key = source.file + ':' + line;
  const pendingKey = appid + ':' + key;
  if (state.pendingBps.has(pendingKey)) return;
  state.pendingBps.add(pendingKey);
  if (!state.bps.has(appid)) state.bps.set(appid, new Set());
  const bps = state.bps.get(appid), removing = bps.has(key);
  try { const result = await post('/api/breakpoint', { appid, file: source.file, line, remove: removing }); if (!result.ok) throw new Error(result.error || '设置断点失败'); removing ? bps.delete(key) : bps.add(key); if (appid === state.selected) renderSource(line); }
  catch (error) { toast(error.message, true); }
  finally { state.pendingBps.delete(pendingKey); }
}
async function openSourceAt(file, line) {
  switchTab('source');
  await loadFileList();
  const option = [...$('src-file').options].find(item => item.value.endsWith(file) || file.endsWith(item.value));
  if (option) { $('src-file').value = option.value; await loadSource(Number(line)); }
  else toast('未找到对应源码文件', true);
}
function renderDiagnostics() {
  const status = currentStatus();
  $('diagnostic-values').innerHTML = pairs({ 'AppID': state.selected || '--', '状态': phaseName(status), 'Agent': status.agent_version ? 'v' + status.agent_version : '--', '最近心跳': status.heartbeat_at ? `${ts(status.heartbeat_at)} · ${ago(status.heartbeat_at)}` : '--', '最近事件': ts(status.last_event_at), '失败次数': status.attempts || 0, '下次重试': status.retry_in ? `${Math.ceil(status.retry_in)} 秒后` : '--', '最近原因': status.reason || '--', 'Target ID': target()?.targetId || '--', 'Debugger': status.debugger ? '已启用' : '未启用' }).replace(/^<dl>|<\/dl>$/g, '');
  $('capture-values').innerHTML = pairs({ '请求体上限': state.settings.request_body + ' 字符', '响应体上限': state.settings.response_body + ' 字符', '请求头上限': state.settings.headers + ' 字符', '旧捕获钩子': status.capture_legacy?.join(', ') || '无' }).replace(/^<dl>|<\/dl>$/g, '');
  $('diagnostic-sync').textContent = state.sync;
}
async function openSettings() {
  try { state.settings = (await api('/api/settings')).settings; fillSettings(state.settings); $('settings-error').classList.add('hidden'); $('settings-dialog').showModal(); }
  catch (error) { toast(error.message, true); }
}
function fillSettings(config) { for (const [key, value] of Object.entries(config)) $('settings-form').elements.namedItem(key).value = value; }
async function saveSettings(event) {
  event.preventDefault(); $('settings-save').disabled = true;
  try { const settings = Object.fromEntries([...new FormData(event.currentTarget)].map(([key, value]) => [key, Number(value)])); const result = await post('/api/settings', { settings }); if (!result.ok) throw new Error('保存失败'); state.settings = result.settings; $('settings-dialog').close(); toast('捕获设置已保存'); renderDiagnostics(); }
  catch (error) { $('settings-error').textContent = error.message; $('settings-error').classList.remove('hidden'); }
  finally { $('settings-save').disabled = false; }
}
async function poll() {
  if (polling) return;
  polling = true; clearTimeout(pollTimer);
  let backlog = false;
  try {
    const status = await api('/api/status');
    state.status = { targets: [], mps: {}, ...status };
    if (status.capture_settings) state.settings = { ...state.settings, ...status.capture_settings };
    renderApps();
    for (let batch = 0; batch < 5; batch++) {
      const query = new URLSearchParams({ paged: '1', limit: '200', since: String(state.since) });
      if (state.cursor) query.set('cursor', state.cursor);
      const page = await api('/api/events?' + query);
      if (page.reset) toast('较早历史已轮换，已接续保留记录');
      store.ingest(page.events || []);
      state.cursor = page.cursor;
      if (state.cursor) state.since = 0;
      backlog = !!page.has_more;
      if (!backlog) break;
    }
    state.sync = (backlog ? '同步历史中 · ' : '已同步 · ') + ts(Date.now() / 1000);
    $('connection-dot').className = 'dot on'; $('connection-label').textContent = '本地服务已连接';
    renderActive();
  } catch (error) { state.sync = '同步失败 · ' + error.message; $('connection-dot').className = 'dot off'; $('connection-label').textContent = '本地服务连接失败'; }
  finally { $('sync-status').textContent = state.sync; $('diagnostic-sync').textContent = state.sync; polling = false; pollTimer = setTimeout(poll, backlog ? 100 : 1500); }
}

$('apps').addEventListener('click', event => { const item = event.target.closest('[data-app]'); if (item) selectApp(item.dataset.app); });
document.querySelector('.tabs').addEventListener('click', event => { const tab = event.target.closest('[data-tab]'); if (tab) switchTab(tab.dataset.tab); });
document.querySelector('.tabs').addEventListener('keydown', event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const tabs = [...document.querySelectorAll('.tab')].filter(tab => !tab.hidden); let index = tabs.findIndex(tab => tab.dataset.tab === state.tab); index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; switchTab(tabs[index].dataset.tab); tabs[index].focus(); });
for (const name of ['net-filter', 'net-method', 'net-state']) $(name).addEventListener(name === 'net-filter' ? 'input' : 'change', () => renderNetwork(true));
for (const name of ['con-filter', 'con-level', 'con-follow']) $(name).addEventListener(name === 'con-filter' ? 'input' : 'change', () => renderConsole(true));
$('net-rows').addEventListener('click', event => { const row = event.target.closest('[data-request]'); if (row) { state.netSel = row.dataset.request; renderNetwork(true); } });
$('net-rows').addEventListener('keydown', event => { if (event.key === 'Enter') event.target.closest('[data-request]')?.click(); });
$('net-detail').addEventListener('click', event => {
  const tab = event.target.closest('[data-detail-tab]'), action = event.target.closest('[data-detail-action]')?.dataset.detailAction;
  const row = store.app(state.selected).network.get(state.netSel);
  if (!row) return;
  if (tab) { state.detailTab = tab.dataset.detailTab; renderDetail(row); }
  if (action === 'close') { state.netSel = null; renderNetwork(true); }
  if (action === 'url') copy(row.url);
  if (action === 'curl') { copy(curlCommand(row)); if (truncated(row)) toast('已复制 cURL；捕获内容有截断'); }
  if (action === 'body') { copy(bodyText(row.response?.data?.data)); if (row.response?.data?.capture?.body?.truncated) toast('已复制已截断的响应体'); }
  if (action === 'download') download('response-' + row.id + '.json', { url: row.url, capture: row.response?.data?.capture, body: row.response?.data?.data });
});
$('clear-network').onclick = () => { store.clearNetwork(state.selected); state.netSel = null; renderNetwork(true); renderSession(); };
$('export-network').onclick = () => download(`network-${state.selected || 'session'}.json`, { appid: state.selected, exported_at: new Date().toISOString(), requests: filteredRows() });
$('clear-console').onclick = () => { store.app(state.selected).console = []; state.frozenConsole = []; renderConsole(true); renderSession(); };
$('export-console').onclick = () => download(`console-${state.selected || 'session'}.json`, consoleRows());
$('pause-console').onclick = () => { state.consolePaused = !state.consolePaused; state.frozenConsole = [...store.app(state.selected).console]; const label = state.consolePaused ? '恢复显示' : '暂停显示'; $('pause-console').innerHTML = icon(state.consolePaused ? 'play' : 'pause'); $('pause-console').setAttribute('aria-label', label); $('pause-console').dataset.tip = label; icons(); renderConsole(true); };
$('console-scroll').addEventListener('click', event => { const link = event.target.closest('[data-source-ref]'); if (link) openSourceAt(link.dataset.sourceRef, link.dataset.line); });
$('repl-form').onsubmit = sendEval;
$('storage-filter').oninput = renderStorage;
$('storage-refresh').onclick = loadStorage;
$('export-storage').onclick = () => download(`storage-${state.selected || 'session'}.json`, state.storage[state.selected] || {});
$('st-rows').addEventListener('click', event => { const key = event.target.closest('[data-storage-copy]')?.dataset.storageCopy; if (key !== undefined) copy(bodyText(state.storage[state.selected]?.kv?.[key])); });
$('src-file').onchange = () => loadSource();
$('src-goto').onkeydown = event => { if (event.key === 'Enter') renderSource(Number(event.target.value)); };
$('src-raw').onclick = () => { if (state.source) { state.source.formatted = false; renderSource(); } };
$('src-format').onclick = () => { if (!state.source) return; if (!window.js_beautify) { toast('格式化组件不可用', true); return; } state.source.pretty = js_beautify(state.source.lines.join('\n'), { indent_size: 2 }).split('\n'); state.source.formatted = true; renderSource(); };
$('src-scroll').addEventListener('click', event => { const line = event.target.closest('[data-breakpoint]')?.dataset.breakpoint; if (line) toggleBreakpoint(Number(line)); });
$('source-copy').onclick = () => { if (state.source) copy((state.source.formatted ? state.source.pretty : state.source.lines).join('\n')); };
$('source-download').onclick = () => { if (state.source) download(state.source.file.split('/').pop(), state.source.lines.join('\n'), 'text/javascript'); };
$('settings-open').onclick = openSettings;
$('settings-close').onclick = () => $('settings-dialog').close();
$('settings-reset').onclick = () => fillSettings({ request_body: 32768, response_body: 131072, headers: 16384 });
$('settings-form').onsubmit = saveSettings;
$('refresh-status').onclick = poll;
$('start-h5').onclick = async () => {
  $('start-h5').disabled = true;
  try { await post('/api/h5/start', {}); toast('网页服务启动中'); }
  catch (error) { toast(error.message, true); }
  finally { poll(); }
};
$('retry-agent').onclick = async () => {
  if (isChiiTarget(target())) { delete $('h5-devtools').dataset.target; renderSession(); poll(); return; }
  try { await post('/api/retry', { appid: state.selected }); toast('已安排重试'); poll(); }
  catch (error) { toast(error.message, true); }
};
$('open-devtools').onclick = async () => {
  const item = target(); if (!item) return;
  if (isChiiTarget(item)) {
    window.open(chiiUrl(item), '_blank', 'noopener');
    return;
  }
  try { await api('/api/open_devtools?' + new URLSearchParams({ target_id: item.targetId, appid: item.appid })); }
  catch (error) { toast(error.message, true); }
};
document.querySelectorAll('[data-debug]').forEach(button => { button.onclick = async () => { try { const result = await post('/api/control', { appid: state.selected, action: button.dataset.debug }); if (!result.ok) throw new Error('调试命令失败'); poll(); } catch (error) { toast(error.message, true); } }; });
switchTab('network'); icons(); poll();

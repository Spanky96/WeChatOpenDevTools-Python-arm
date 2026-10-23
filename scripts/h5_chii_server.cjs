'use strict';

const http = require('node:http');
const { createRequire } = require('node:module');
const { randomUUID } = require('node:crypto');
const { parseArgs } = require('node:util');

const CHII_VERSION = '1.15.5';
const HOST = '127.0.0.1';
const chiiRequire = createRequire(require.resolve('chii/package.json'));
const chiiPackage = chiiRequire('./package.json');
const Koa = chiiRequire('koa');
const WebSocketServer = chiiRequire('./server/lib/WebSocketServer');
const router = chiiRequire('./server/middle/router');
const compress = chiiRequire('./server/middle/compress');
const targetConnections = new WeakMap();

if (chiiPackage.version !== CHII_VERSION) {
  throw new Error(`Expected chii ${CHII_VERSION}; found ${chiiPackage.version}. Run npm ci.`);
}

function shortString(value, limit = 8192) {
  return typeof value === 'string' ? value.slice(0, limit) : '';
}

function targetList(channelManager, port) {
  const origin = `http://${HOST}:${port}`;
  return Object.values(channelManager.getTargets()).map(target => {
    if (!targetConnections.has(target)) targetConnections.set(target, randomUUID());
    const id = String(target.id);
    const endpoint = `${HOST}:${port}/client/${randomUUID()}?target=${encodeURIComponent(id)}`;
    return {
      id,
      title: shortString(target.title, 1024),
      url: shortString(target.url),
      favicon: shortString(target.favicon),
      ip: shortString(target.ip, 64),
      userAgent: shortString(target.userAgent, 2048),
      rtc: false,
      transport: 'chii',
      connectionId: targetConnections.get(target),
      devtoolsFrontendUrl: `${origin}/front_end/chii_app.html?ws=${encodeURIComponent(endpoint)}`,
      webSocketDebuggerUrl: `ws://${endpoint}`,
    };
  }).reverse();
}

function createServer({ port = 8780 } = {}) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('Port must be an integer between 1 and 65535');
  }
  const app = new Koa();
  const wss = new WebSocketServer();
  const channelManager = wss.channelManager;
  app.use(async (ctx, next) => {
    ctx.set('Cache-Control', 'no-store');
    if (ctx.method === 'GET' && ctx.path === '/health') {
      ctx.body = {
        service: 'wechat-h5-chii',
        chiiVersion: CHII_VERSION,
        pid: process.pid,
        host: HOST,
        port,
        targetCount: Object.keys(channelManager.getTargets()).length,
      };
    } else if (ctx.method === 'GET' && ctx.path === '/targets') {
      ctx.body = { targets: targetList(channelManager, port) };
    } else if (ctx.method === 'GET' && ctx.path === '/json/list') {
      ctx.body = targetList(channelManager, port).map(target => ({
        ...target,
        type: 'page',
        description: 'Chii JavaScript instrumentation; not native Chrome CDP',
      }));
    } else {
      await next();
    }
  });
  app.use(compress()).use(router(channelManager, `${HOST}:${port}`, null, '/'));
  const server = http.createServer(app.callback());
  // Chii's upstream upgrade handler throws on a missing query string.
  // Parse and validate before handing a connection to its channel manager.
  server.on('upgrade', (request, socket, head) => {
    let parsed;
    let match;
    try {
      parsed = new URL(request.url, `http://${HOST}:${port}`);
      match = /^\/(target|client)\/([A-Za-z0-9_-]{1,128})$/.exec(parsed.pathname);
      if (!match || (match[1] === 'client' && !parsed.searchParams.get('target')) ||
          (match[1] === 'target' && !/^https?:\/\//.test(parsed.searchParams.get('url') || ''))) {
        throw new Error('Invalid debug endpoint');
      }
    } catch (_) {
      socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      return;
    }
    wss._wss.handleUpgrade(request, socket, head, ws => {
      ws.type = match[1];
      ws.id = match[2];
      if (ws.type === 'target') {
        ws.chiiUrl = parsed.searchParams.get('url');
        ws.title = parsed.searchParams.get('title') || '';
        ws.favicon = parsed.searchParams.get('favicon') || '';
        ws.rtc = false;
      } else {
        ws.target = parsed.searchParams.get('target');
      }
      wss._wss.emit('connection', ws, request);
    });
  });
  return { server, channelManager, webSocketServer: wss._wss };
}

function main() {
  const { values } = parseArgs({
    options: {
      port: { type: 'string', default: '8780' },
      host: { type: 'string', default: HOST },
      help: { type: 'boolean', default: false },
    },
  });
  if (values.help) {
    process.stdout.write('Usage: node scripts/h5_chii_server.cjs [--port 8780] [--host 127.0.0.1]\n');
    return;
  }
  if (values.host !== HOST) {
    throw new Error('H5 debugging binds only to 127.0.0.1');
  }
  const port = Number(values.port);
  const { server, webSocketServer } = createServer({ port });
  server.on('error', error => {
    process.stderr.write(`H5 server: ${error.message}\n`);
    process.exitCode = 1;
  });
  let stopping = false;
  function stop() {
    if (stopping) return;
    stopping = true;
    // Chii deliberately does not reconnect after close codes 1000 or 1001.
    for (const client of webSocketServer.clients) client.close(1012, 'Server restarting');
    server.close(() => process.exit(0));
    setTimeout(() => {
      for (const client of webSocketServer.clients) client.terminate();
      process.exit(0);
    }, 2000).unref();
  }
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  server.listen(port, HOST, () => {
    process.stdout.write(`WeChat H5 Chii ${CHII_VERSION}: http://${HOST}:${port}/\n`);
  });
}

module.exports = { createServer, targetList };

if (require.main === module) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`H5 server: ${error.message}\n`);
    process.exitCode = 1;
  }
}

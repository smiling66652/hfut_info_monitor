/**
 * cdp_fetch.mjs — 通过 CDP 直连（真实/无头）Edge 获取 JS 渲染后的页面 HTML
 * 用法: node cdp_fetch.mjs <url>
 * 环境变量:
 *   HFUT_CDP_PORT    调试端口 (默认 9333，避开用户主 Edge 的 9222)
 *   HFUT_CDP_PROFILE 无头实例用户目录 (默认系统临时目录)
 * 行为:
 *   1. 若 127.0.0.1:PORT 已有调试端点 → 直接复用（不干扰用户正在用的 Edge）
 *   2. 否则自动拉起一个无头 Edge（headless=new）并在退出时关闭
 * 实现: 手写 WebSocket（http.request 升级 + 帧解析），不依赖 undici 全局
 *       WebSocket（该实现在本机会被代理 dispatcher 拦截，导致 ws 升级失败）
 * 输出: 渲染后的完整 HTML 写到 stdout；错误写到 stderr 并以退出码 1 结束
 */
import { spawn } from 'node:child_process';
import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// 捕获校园代理并保留给「拉起的无头 Edge」使用（无头 Edge 需经校园代理才能达
// xc/zb 等 hfut 子域）；但本 Node 进程自己对 127.0.0.1 的请求用手写 http.request
// （不走代理），所以这里把代理从 process.env 删掉，仅透传给子进程 Edge。
const _CAMPUS_PROXY =
  process.env.HTTP_PROXY || process.env.http_proxy ||
  process.env.HTTPS_PROXY || process.env.https_proxy || '';
for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                 'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy']) {
  delete process.env[k];
}

// 构造传给「拉起的无头 Edge」的环境：恢复校园代理（xc/zb 需经校园代理/WebVPN 才可达），
// 但不要把 NO_PROXY 传进去（否则代理不生效）。本 Node 进程自己对 127.0.0.1 仍走直连。
const edgeEnv = { ...process.env };
if (_CAMPUS_PROXY) {
  edgeEnv.HTTP_PROXY = _CAMPUS_PROXY;
  edgeEnv.HTTPS_PROXY = _CAMPUS_PROXY;
  edgeEnv.http_proxy = _CAMPUS_PROXY;
  edgeEnv.https_proxy = _CAMPUS_PROXY;
}
delete edgeEnv.NO_PROXY;
delete edgeEnv.no_proxy;

const TARGET_URL = process.argv[2];
if (!TARGET_URL) {
  process.stderr.write('usage: node cdp_fetch.mjs <url>\n');
  process.exit(2);
}
const PORT = process.env.HFUT_CDP_PORT || '9333';
const BASE = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function httpGet(u) {
  return new Promise((res, rej) => {
    const r = http.get(u, { headers: { Host: 'localhost' } }, (resp) => {
      let d = '';
      resp.on('data', (c) => (d += c));
      resp.on('end', () => res(d));
    });
    r.on('error', rej);
    r.setTimeout(5000, () => r.destroy(new Error('http timeout')));
  });
}

function findEdge() {
  const cand = [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge Beta\\Application\\msedge.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge Beta\\Application\\msedge.exe',
  ];
  for (const c of cand) if (fs.existsSync(c)) return c;
  return null;
}

let launchedChild = null;
async function ensureBrowser() {
  try {
    const v = await httpGet(`${BASE}/json/version`);
    if (v && v.includes('webSocketDebuggerUrl')) return false;
  } catch (_) {}
  const edge = findEdge();
  if (!edge) throw new Error('未找到 Edge 可执行文件，无法拉起无头实例');
  const prof = process.env.HFUT_CDP_PROFILE || path.join(os.tmpdir(), 'hfut_cdp_profile');
  fs.mkdirSync(prof, { recursive: true });
  launchedChild = spawn(
    edge,
    ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${prof}`,
     '--no-first-run', '--no-default-browser-check', 'about:blank'],
    { stdio: 'ignore', detached: true, env: edgeEnv }
  );
  for (let i = 0; i < 50; i++) {
    await sleep(400);
    try {
      const v = await httpGet(`${BASE}/json/version`);
      if (v && v.includes('webSocketDebuggerUrl')) return true;
    } catch (_) {}
  }
  throw new Error('无头 Edge 拉起超时');
}

// ── 手写最小 WebSocket 客户端（绕过 undici 代理 dispatcher）──
function wsConnect(wsUrl) {
  return new Promise((resolve, reject) => {
    const u = new URL(wsUrl);
    const port = u.port || PORT; // 浏览器级 ws URL 常省略端口，复用 HTTP 调试端口
    const key = crypto.randomBytes(16).toString('base64');
    const req = http.request({
      host: u.hostname,
      port,
      path: u.pathname + u.search,
      headers: {
        Connection: 'Upgrade',
        Upgrade: 'websocket',
        'Sec-WebSocket-Key': key,
        'Sec-WebSocket-Version': '13',
      },
    });
    req.on('upgrade', (res, socket, head) => {
      if (res.statusCode !== 101) {
        reject(new Error('ws 升级返回 ' + res.statusCode));
        socket.destroy();
        return;
      }
      resolve({ socket, head: head && head.length ? head : Buffer.alloc(0) });
    });
    req.on('response', (res) => reject(new Error('ws 非升级响应 ' + res.statusCode)));
    req.on('error', reject);
    req.end();
  });
}

function sendText(socket, str) {
  const payload = Buffer.from(str, 'utf8');
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x81, 0x80 | len]);
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 0x80 | 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 0x80 | 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  const mask = crypto.randomBytes(4);
  const masked = Buffer.alloc(len);
  for (let i = 0; i < len; i++) masked[i] = payload[i] ^ mask[i % 4];
  socket.write(Buffer.concat([header, mask, masked]));
}

// 解析服务端发来的文本帧（未掩码），返回剩余未消费字节
function parseFrames(buf, onMessage, onPing) {
  let offset = 0;
  while (offset + 2 <= buf.length) {
    const b0 = buf[offset];
    const b1 = buf[offset + 1];
    const opcode = b0 & 0x0f;
    const masked = (b1 & 0x80) !== 0;
    let len = b1 & 0x7f;
    let p = offset + 2;
    if (len === 126) {
      if (buf.length < p + 2) break;
      len = buf.readUInt16BE(p);
      p += 2;
    } else if (len === 127) {
      if (buf.length < p + 8) break;
      len = Number(buf.readBigUInt64BE(p));
      p += 8;
    }
    let maskKey = null;
    if (masked) {
      if (buf.length < p + 4) break;
      maskKey = buf.slice(p, p + 4);
      p += 4;
    }
    if (buf.length < p + len) break; // 帧未完整，等更多数据
    let payload = buf.slice(p, p + len);
    if (masked && maskKey) {
      const un = Buffer.alloc(len);
      for (let i = 0; i < len; i++) un[i] = payload[i] ^ maskKey[i % 4];
      payload = un;
    }
    offset = p + len;
    if (opcode === 1) onMessage(payload.toString('utf8'));
    else if (opcode === 9 && onPing) onPing(payload);
  }
  return buf.slice(offset);
}

async function main() {
  await ensureBrowser();
  let socket = null;
  let targetId = null;
  let closed = false;
  try {
    const ver = JSON.parse(await httpGet(`${BASE}/json/version`));
    const bwUrl = ver.webSocketDebuggerUrl.replace('localhost', '127.0.0.1');
    if (!bwUrl) throw new Error('/json/version 未返回 webSocketDebuggerUrl');

    const conn = await wsConnect(bwUrl);
    socket = conn.socket;
    let buffer = Buffer.from(conn.head);
    const pending = new Map();
    let msgId = 0;
    let loadDone = false;

    const send = (method, params = {}, sessionId = null) =>
      new Promise((resolve) => {
        const id = ++msgId;
        pending.set(id, resolve);
        sendText(socket, JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
      });

    // ping → 回 pong（不掩码），保持连接
    const onPing = (payload) => {
      const header = Buffer.from([0x8a, payload.length < 126 ? payload.length : 126]);
      const body =
        payload.length < 126
          ? payload
          : (() => {
              const h = Buffer.alloc(2);
              h.writeUInt16BE(payload.length, 0);
              return Buffer.concat([h, payload]);
            })();
      socket.write(Buffer.concat([header, body]));
    };

    socket.on('data', (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      buffer = parseFrames(buffer, (msg) => {
        let m;
        try { m = JSON.parse(msg); } catch (_) { return; }
        if (m.id && pending.has(m.id)) {
          pending.get(m.id)(m);
          pending.delete(m.id);
        } else if (m.method === 'Page.loadEventFired') {
          loadDone = true;
        }
      }, onPing);
    });

    // 1) 开新标签页并加载目标 URL
    const cr = await send('Target.createTarget', { url: TARGET_URL });
    targetId = cr.result.targetId;
    const at = await send('Target.attachToTarget', { targetId, flatten: true });
    const curSession = at.result.sessionId;
    await send('Page.enable', {}, curSession);
    await send('Runtime.enable', {}, curSession);
    for (let i = 0; i < 60 && !loadDone; i++) await sleep(400);
    await sleep(5000); // SPA（Vue/Nuxt/bysjy）需要更久渲染列表
    const ev = await send(
      'Runtime.evaluate',
      { expression: 'document.documentElement.outerHTML', returnByValue: true, awaitPromise: false },
      curSession
    );
    const html = ev && ev.result && ev.result.result ? ev.result.result.value : '';
    if (!html) throw new Error('Runtime.evaluate 未返回 HTML');
    process.stdout.write(html);
  } catch (e) {
    process.stderr.write('CDP_FETCH_ERR ' + (e && e.message ? e.message : String(e)) + '\n');
    process.exitCode = 1;
  } finally {
    if (targetId && socket && !closed) {
      try { await send('Target.closeTarget', { targetId }); } catch (_) {}
    }
    if (socket) {
      try { socket.end(); socket.destroy(); } catch (_) {}
    }
    closed = true;
    if (launchedChild) {
      try { launchedChild.kill(); } catch (_) {}
    }
  }
}

main();
